# Fork maintenance

This fork follows upstream `main`. Local work is committed directly on `main`.
Home-server deployment configuration and credentials live outside this public
repository; never copy runtime secrets or personal conversation data here.

This checkout fetches `origin` from GitHub and has two `origin.pushurl` entries:
the GitHub fork and its Gitea mirror. `remote.pushDefault=origin` makes an ordinary
`git push` send the current branch to both, sequentially. The updates are
independent: if one fails, inspect both remotes before retrying. These settings
are local Git configuration and must be reapplied in a new clone.

The saved-chat navigation change is in
`src/lib/components/chat/Chat.svelte` and `src/lib/utils/chatSnapshot.ts`.
It joins the realtime room before the initial snapshot, loads tags in parallel,
and starts task reconciliation after the snapshot arrives. Retain reconnect,
refused/late-join refreshes, abort handling and the live task-state guard.
Do not remove reasoning/tool history or cache authenticated snapshots globally.

Validate frontend changes with:

```bash
ONNXRUNTIME_NODE_INSTALL_CUDA=skip npm ci --force
npm run test:frontend -- --run
npm run check
npm run build
```

Use the existing upstream Dockerfile for source builds; `USE_CUDA=true` selects
the home deployment's CUDA variant. Home Compose orchestration remains in
`/home/adrian/docker` and points its build context to this checkout.

## Account-specific Honcho memory

`utils/honcho.py` binds the explicitly enrolled administrator account to the
private connector. Other users retain native memory. The per-model
`meta.honcho_write_enabled` flag defaults false; administrators alone can
change it through create/update/import/restore paths. The stored model entry,
not client capabilities, decides write permission. Enforce it both when
registering tools and when executing them/capturing completions. Temporary,
internal, direct, automation and task traffic is excluded. Capture only the
current finalized turn, never regenerate/backfill excluded history.

The model editor exposes the update toggle; Personalization shows the shared
profile for enrolled accounts while retaining native rows as rollback data.
`routers/honcho.py` must reject other accounts. Native Responses prefetch keeps
the existing `recall_memories` name and relay contract. Deployment, connector,
credential handling and migration evidence live in `/home/adrian/docker`.
Run isolated `backend/tests/test_honcho.py` and connector tests in addition to
the relevant existing checks. See `docs/honcho.md` for behavior and limitations.

## PostgreSQL chat search

The search changes are in `routers/search.py`, `models/chats.py`,
`utils/chat_search.py` and migration `b72c4a9e31d6` under
`backend/open_webui/`. PostgreSQL uses STORED search projections; SQLite keeps
its existing query/schema behavior. Keep generated columns out of portable ORM
insert payloads, preserve both APIs' different partial-import/ownership rules,
and confirm joined-text prefilters against individual messages. Do not remove
sharing checks, snapshot boundaries or Chinese short substring matching.

See `docs/chat-search.md` for the migration's table-rewrite/locking costs,
extension-permission fallback, measurements and rollback. Switching to an older
upstream image requires downgrading this fork's Alembic revision first; preserve
applied IDs and reconcile future upstream migration heads deliberately.

Backend checks (use a disposable empty PostgreSQL database, never deployment
credentials):

```bash
PYTHONPATH=backend ENABLE_DB_MIGRATIONS=false WEBUI_SECRET_KEY=isolated-tests-only \
  DATABASE_URL=postgresql://postgres@localhost/chat_search_test \
  CHAT_SEARCH_TEST_DATABASE_URL=postgresql://postgres@localhost/chat_search_test \
  python -m unittest discover -s backend/tests -v
ruff format --check backend
ruff check --select=F --ignore=F401,F403,F405,F541,F811,F841 backend
```
