"""Persist PostgreSQL substring-search projections without changing source data.

Revision ID: b72c4a9e31d6
Revises: 9f6c2a8d1b43

Stored columns are deliberately not mapped on the portable ORM models: SQLite
retains its existing JSON predicates, and bulk message inserts must not try to
write generated values. PostgreSQL computes them transactionally on every write.
"""

import logging

from alembic import op
import sqlalchemy as sa

revision = 'b72c4a9e31d6'
down_revision = '9f6c2a8d1b43'
branch_labels = None
depends_on = None

log = logging.getLogger(__name__)


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name != 'postgresql':
        return

    # JSON accepts escaped NULs and unpaired surrogates that PostgreSQL text
    # cannot represent. Preserve those source values, omitting only their search
    # projection instead of rejecting a previously valid write or migration.
    op.execute(r"""
        CREATE FUNCTION owui_message_search_text(body json) RETURNS text
        LANGUAGE plpgsql IMMUTABLE STRICT AS $$
        BEGIN
            IF json_typeof(body) = 'string' THEN
                RETURN lower(body #>> '{}');
            END IF;
            RETURN NULL;
        EXCEPTION WHEN untranslatable_character OR invalid_text_representation OR character_not_in_repertoire THEN
            RETURN NULL;
        END;
        $$
    """)
    op.execute(r"""
        CREATE FUNCTION owui_chat_search_messages(body json) RETURNS text[]
        LANGUAGE plpgsql IMMUTABLE STRICT AS $$
        DECLARE
            messages text[];
        BEGIN
            SELECT array_agg(content) FILTER (WHERE content IS NOT NULL) INTO messages
            FROM (
                SELECT owui_message_search_text(m.value->'content') AS content
                FROM json_each(CASE WHEN json_typeof(body#>'{history,messages}') = 'object'
                    THEN body#>'{history,messages}' ELSE '{}'::json END) AS m
                WHERE json_typeof(m.value->'content') = 'string'
                UNION ALL
                SELECT owui_message_search_text(m->'content') AS content
                FROM json_array_elements(CASE WHEN json_typeof(body->'messages') = 'array'
                    THEN body->'messages' ELSE '[]'::json END) AS m
                WHERE json_typeof(m->'content') = 'string'
            ) AS contents;
            RETURN messages;
        EXCEPTION WHEN untranslatable_character OR invalid_text_representation OR character_not_in_repertoire THEN
            RETURN NULL;
        END;
        $$
    """)
    op.execute(r"""
        CREATE FUNCTION owui_chat_search_join(messages text[]) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        AS $$ SELECT array_to_string(messages, E'\n') $$
    """)
    op.add_column(
        'chat_message',
        sa.Column(
            'owui_search_text',
            sa.Text(),
            sa.Computed('owui_message_search_text(content)', persisted=True),
        ),
    )
    # One ALTER avoids rewriting the potentially large chat table twice.
    op.execute(r"""
        ALTER TABLE chat
        ADD COLUMN owui_search_messages text[]
            GENERATED ALWAYS AS (owui_chat_search_messages(chat)) STORED,
        ADD COLUMN owui_search_safe boolean
            GENERATED ALWAYS AS (chat::text NOT LIKE '%\\u0000%') STORED
    """)

    # Managed databases may forbid installing extensions. A savepoint keeps a
    # denied CREATE EXTENSION from aborting the required text-column migration.
    # Precomputed text still speeds up short patterns without a trigram index.
    have_trigrams = True
    try:
        with conn.begin_nested():
            op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    except sa.exc.DBAPIError:
        log.warning('pg_trgm unavailable; chat search will scan its stored text projections')
        have_trigrams = False
    if have_trigrams:
        extension_schema = conn.execute(
            sa.text(
                "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pg_trgm'"
            )
        ).scalar_one()
        opclass = conn.dialect.identifier_preparer.quote_identifier(extension_schema) + '.gin_trgm_ops'
        op.execute(f'CREATE INDEX ix_chat_message_search_trgm ON chat_message USING gin (owui_search_text {opclass})')
        op.execute(
            'CREATE INDEX ix_chat_search_messages_trgm ON chat '
            f'USING gin (owui_chat_search_join(owui_search_messages) {opclass})'
        )
    # New generated columns initially lack statistics. Otherwise PostgreSQL may
    # keep scanning despite the new indexes until enough future writes trigger
    # auto-analyze. Limit sampling to the small search projections, not chat JSON.
    op.execute('ANALYZE chat_message (owui_search_text)')
    op.execute('ANALYZE chat (owui_search_messages)')


def downgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute('DROP INDEX IF EXISTS ix_chat_search_messages_trgm')
    op.execute('DROP INDEX IF EXISTS ix_chat_message_search_trgm')
    op.drop_column('chat', 'owui_search_safe')
    op.drop_column('chat', 'owui_search_messages')
    op.drop_column('chat_message', 'owui_search_text')
    op.execute('DROP FUNCTION owui_chat_search_join(text[])')
    op.execute('DROP FUNCTION owui_chat_search_messages(json)')
    op.execute('DROP FUNCTION IF EXISTS owui_message_search_text(json)')
    # pg_trgm may be shared with other features; never drop it on downgrade.
