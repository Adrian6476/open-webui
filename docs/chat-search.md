# PostgreSQL chat-search projections

This fork prepares searchable message text when PostgreSQL writes a row instead
of decoding and lowercasing large JSON values on every search. It preserves
case-insensitive substring matching, including one-/two-character Chinese terms.
SQLite retains its existing schema and query paths.

## Storage and query behavior

Migration `b72c4a9e31d6` follows upstream revision `9f6c2a8d1b43` and adds:

- `chat_message.owui_search_text`: the lowercase string content, or NULL for
  non-string/escaped-NUL content.
- `chat.owui_search_messages`: lowercase string contents from both the history
  object and legacy message array, kept as separate entries.
- `chat.owui_search_safe`: the existing legacy escaped-NUL exclusion, evaluated
  once on writes rather than by scanning the entire JSON value on every query.

These are explicitly STORED generated columns. The original columns remain the
source of truth; inserts, edits, imports and deletes cannot leave an asynchronously
maintained search index behind. They are not mapped as portable ORM fields, so
bulk message inserts and API serialization do not write/expose generated values.
Unrepresentable JSON escapes (NULs or unpaired surrogates) preserve the original
source and produce a NULL projection instead of breaking migration or writes.

GIN trigram indexes accelerate patterns with useful trigrams. If PostgreSQL denies
installation of `pg_trgm`, a savepoint preserves the required text migration and
search continues using the precomputed columns without those indexes. Existing
extensions in other schemas are resolved by their qualified operator class.
The migration analyzes only the search columns afterward so the optimizer can
use them immediately without sampling the large original chat JSON.

Workspace search retains its normalized-message-first rule, including its current
partial-import behavior. Its legacy JSON fallback reads stored per-message text.
Reference Chats and the model's history-search tool retain their different rule:
search both normalized rows with matching ownership and the historical chat body.
A joined-text trigram prefilter is confirmed against individual array entries so
a phrase spanning two messages never becomes a false positive. Literal wildcard
escaping in workspace search and the legacy API's pattern semantics are preserved.
Multiword terms can still match different messages or the title. Authorization,
folder scope, sharing snapshots, ordering and pagination are unchanged.

## Migration and rollback

This is a fork-specific backend and schema change, not an upstream setting.
**Do not deploy it as a zero-downtime migration.** Adding STORED columns rewrites
existing tables; normal index creation holds locks until the migration commits.
The two chat columns are added in one ALTER to avoid a second chat-table rewrite.
Schedule a maintenance window with no active generations, take a verified backup,
and rehearse on a database copy with sufficient disk and memory headroom. The measured full-copy
rehearsal used a disposable PostgreSQL container limited to 3 GiB of memory.
Production was not involved.

The first full-copy rehearsal used PostgreSQL 18, roughly 3,400 chats and 25,000
messages. Upgrade took about 146 seconds in the disposable environment, with
fsync disabled. The final downgrade/upgrade rehearsal also compared checksums
of every original chat/message field and found no changes; its upgrade plus
checksum pass took about 170 seconds. This is not a production downtime estimate. Search projections
occupied about 49 MB and 53 MB compressed; their indexes occupied 146 MB and
87 MB. Writes and imports pay extra extraction/index/WAL costs. Extremely large
legacy chat JSON still needs parsing when that source column changes. Future
capacity tests should include those writes and the deployment's backup pipeline.

Normal fork startup applies the Alembic migration. If migrations are disabled,
apply it explicitly before running this version; the new predicates require the
new columns. Do not edit a running container or run migrations against a live
service merely to try the patch.

To return to the pre-search version, stop application writes and **use this fork's
migration code to downgrade to `9f6c2a8d1b43` before switching images**. The downgrade
drops only search projections, their indexes and helper functions; it retains
original messages/chats and leaves the shared `pg_trgm` extension installed.
An older upstream image does not know this fork's revision, so switching images
without handling the migration history can prevent startup. On a fork that has
subsequently added migrations, follow their dependency order rather than blindly
running this older target.

Keep applied migration IDs stable when merging upstream updates. If upstream
adds a parallel migration head, reconcile the graph with a proper merge revision
and test upgrade/downgrade on a restored copy. Do not remove a deployed revision,
change its parent retrospectively or delete original conversation columns. The
full-copy validation compares original-field checksums across downgrade/upgrade;
this does not promise compatibility with arbitrary future upstream migrations.

## Validation

Run with the backend dependencies installed. The PostgreSQL test database must
be disposable and empty, using a test administrator that can create schemas and
roles. The suite refuses an existing public application `chat` table and never
reads real conversations. The denied-extension test exercises actual PostgreSQL
permissions/savepoint recovery. Do not point it at a deployment database.

```bash
PYTHONPATH=backend \
  ENABLE_DB_MIGRATIONS=false \
  WEBUI_SECRET_KEY=isolated-tests-only \
  DATABASE_URL=postgresql://postgres@localhost/chat_search_test \
  CHAT_SEARCH_TEST_DATABASE_URL=postgresql://postgres@localhost/chat_search_test \
  python -m unittest discover -s backend/tests -v
```

The ten tests cover backfill, updates, non-string and escaped-NUL content,
literal `%`/`_`/backslashes, invalid Unicode, message boundaries, downgrade/re-upgrade, denied extensions,
partial imports, ownership, real bulk writes, cross-message multiword search,
Chinese short terms, pagination/ranking, archived/internal chats, shared folders,
snapshot isolation, access revocation and deletion. The workspace behavior suite
also runs against SQLite. A PostgreSQL plan check exercises the real candidate
query with a synthetic message fixture and verifies trigram-index eligibility;
it avoids a machine-dependent response-time assertion. No frontend or API contract changes are required.

On one private full database copy, two sequential samples per workspace query
produced the following measurements through the real application search function,
including permission queries, pagination and response construction:

| Query | Original | Candidate after statistics update |
|---|---:|---:|
| `docker` | 2.94–3.99 s | 0.24–0.38 s |
| `的` | 1.97–1.99 s | 0.30–0.38 s |
| `docker compose nginx` | 7.06–7.23 s | 0.56–0.60 s |
| Absent diagnostic term | 2.92–3.00 s | 0.14–0.15 s |
| Legacy absent term, one sample | 72.39 s | 1.64 s |

Workspace results, excerpts, ordering and pagination matched serialized baselines
for all four query pairs. Plans confirmed bitmap scans on the new message trigram
index. Samples are not p95 estimates, cold-cache or concurrent-load guarantees,
HTTP/browser timings, or an explanation of intermittent public-route latency.
No private data, credentials, result IDs or raw database dump is checked in.
