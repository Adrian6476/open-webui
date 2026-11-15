"""Chat-search integration tests against disposable PostgreSQL and SQLite databases.

Set CHAT_SEARCH_TEST_DATABASE_URL to an empty disposable PostgreSQL database.
The suite creates an isolated schema and never reads application conversations.
Run with: PYTHONPATH=backend python -m unittest discover -s backend/tests -v
"""

import asyncio
import importlib.util
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = Path(__file__).parents[1] / 'open_webui/migrations/versions/b72c4a9e31d6_add_postgres_chat_search_text.py'


def load_migration():
    spec = importlib.util.spec_from_file_location('chat_search_migration', MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SQLiteMigrationTest(unittest.TestCase):
    def test_postgres_projection_does_not_change_sqlite(self):
        engine = sa.create_engine('sqlite://')
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE chat (id text, chat json)')
            with Operations.context(MigrationContext.configure(conn)):
                load_migration().upgrade()
                load_migration().downgrade()
            self.assertEqual([c['name'] for c in sa.inspect(conn).get_columns('chat')], ['id', 'chat'])
        engine.dispose()


@unittest.skipUnless(os.environ.get('CHAT_SEARCH_TEST_DATABASE_URL'), 'disposable PostgreSQL URL required')
class PostgreSQLSearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = sa.create_engine(os.environ['CHAT_SEARCH_TEST_DATABASE_URL'])
        cls.schema = 'test_search_' + uuid.uuid4().hex
        cls.conn = cls.engine.connect()
        if sa.inspect(cls.conn).has_table('chat', schema='public'):
            cls.conn.close()
            cls.engine.dispose()
            raise RuntimeError('Refusing to test against a database with existing application tables')
        cls.conn.exec_driver_sql(f'CREATE SCHEMA {cls.schema}')
        cls.conn.exec_driver_sql(f'SET search_path TO {cls.schema}, public')
        cls.conn.exec_driver_sql('CREATE TABLE chat (id text PRIMARY KEY, user_id text, chat json)')
        cls.conn.exec_driver_sql(
            'CREATE TABLE chat_message (id text PRIMARY KEY, chat_id text, user_id text, content json)'
        )
        cls.conn.execute(
            sa.text('INSERT INTO chat VALUES (:id, :uid, CAST(:body AS json))'),
            {'id': 'backfill', 'uid': 'owner', 'body': '{"messages":[{"content":"Historical 的"}]}'},
        )
        cls.conn.exec_driver_sql(
            """INSERT INTO chat_message VALUES ('backfill-m','backfill','owner','"Historical 的"')"""
        )
        with Operations.context(MigrationContext.configure(cls.conn)):
            load_migration().upgrade()
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.rollback()
        cls.conn.exec_driver_sql('SET search_path TO public')
        cls.conn.exec_driver_sql(f'DROP SCHEMA {cls.schema} CASCADE')
        cls.conn.commit()
        cls.conn.close()
        cls.engine.dispose()

    def setUp(self):
        self.transaction = self.conn.begin()

    def tearDown(self):
        self.transaction.rollback()

    def test_backfill_update_null_and_nonstring_content(self):
        row = self.conn.exec_driver_sql(
            "SELECT owui_search_messages, owui_search_safe FROM chat WHERE id='backfill'"
        ).one()
        self.assertEqual(row.owui_search_messages, ['historical 的'])
        self.assertTrue(row.owui_search_safe)
        self.assertEqual(
            self.conn.exec_driver_sql("SELECT owui_search_text FROM chat_message WHERE id='backfill-m'").scalar(),
            'historical 的',
        )
        self.conn.exec_driver_sql("""INSERT INTO chat_message VALUES ('m','backfill','owner','"MiXeD 配置"')""")
        self.assertEqual(
            self.conn.exec_driver_sql("SELECT owui_search_text FROM chat_message WHERE id='m'").scalar(),
            'mixed 配置',
        )
        for content in ['null', '[]', '{}', '42']:
            self.conn.execute(sa.text("UPDATE chat_message SET content=CAST(:v AS json) WHERE id='m'"), {'v': content})
            self.assertIsNone(
                self.conn.exec_driver_sql("SELECT owui_search_text FROM chat_message WHERE id='m'").scalar()
            )
        self.conn.exec_driver_sql(
            """UPDATE chat SET chat='{"history":{"messages":{"a":{"content":"New Text"}}},"messages":[{"content":"Legacy"}]}' WHERE id='backfill'"""
        )
        self.assertEqual(
            self.conn.exec_driver_sql("SELECT owui_search_messages FROM chat WHERE id='backfill'").scalar(),
            ['new text', 'legacy'],
        )

    def test_unsafe_json_is_preserved_without_breaking_backfill(self):
        body = r'{"messages":[{"content":"bad\u0000value"}]}'
        self.conn.execute(sa.text("UPDATE chat SET chat=CAST(:v AS json) WHERE id='backfill'"), {'v': body})
        row = self.conn.exec_driver_sql(
            "SELECT chat::text, owui_search_messages, owui_search_safe FROM chat WHERE id='backfill'"
        ).one()
        self.assertIn(r'\u0000', row[0])
        self.assertIsNone(row.owui_search_messages)
        self.assertFalse(row.owui_search_safe)

    def test_unrepresentable_unicode_keeps_original_json(self):
        for content in [r'"bad\u0000value"', r'"bad\ud800value"']:
            self.conn.execute(
                sa.text("INSERT INTO chat_message VALUES ('unsafe','backfill','owner',CAST(:body AS json))"),
                {'body': content},
            )
            row = self.conn.exec_driver_sql(
                "SELECT content::text,owui_search_text FROM chat_message WHERE id='unsafe'"
            ).one()
            self.assertEqual(row[0], content)
            self.assertIsNone(row[1])
            self.conn.exec_driver_sql("DELETE FROM chat_message WHERE id='unsafe'")
        body = r'{"messages":[{"content":"bad\ud800value"},{"content":"Valid text"}]}'
        self.conn.execute(sa.text("UPDATE chat SET chat=CAST(:body AS json) WHERE id='backfill'"), {'body': body})
        # PostgreSQL's JSON traversal itself rejects the invalid surrogate, so
        # omit that chat's derived text while preserving its complete source.
        self.assertIsNone(
            self.conn.exec_driver_sql("SELECT owui_search_messages FROM chat WHERE id='backfill'").scalar()
        )
        self.assertEqual(self.conn.exec_driver_sql("SELECT chat::text FROM chat WHERE id='backfill'").scalar(), body)

    def test_literal_backslashes_and_message_boundaries(self):
        from open_webui.routers.search import message_match_sql, pattern

        self.conn.execute(
            sa.text("INSERT INTO chat VALUES ('special','owner',CAST(:body AS json))"),
            {
                'body': r'{"messages":[{"content":"Alpha"},{"content":"Beta"},{"content":"100% a_b u0000 配置的 C:\\temp literal\\u0000"}]}'
            },
        )
        for term, expected in [
            ('alpha', ['special']),
            ('ha\nb', []),
            ('%', ['special']),
            ('_', ['special']),
            ('u0000', ['special']),
            ('配置', ['special']),
            (r'c:\temp', ['special']),
            (r'\u0000', ['special']),
        ]:
            predicate = message_match_sql('chat', 'postgresql', 'q')
            actual = (
                self.conn.execute(
                    sa.text(f"SELECT id FROM chat WHERE id='special' AND {predicate}"), {'q': pattern(term)}
                )
                .scalars()
                .all()
            )
            self.assertEqual(actual, expected, term)
        self.assertFalse(self.conn.exec_driver_sql("SELECT owui_search_safe FROM chat WHERE id='special'").scalar())

    def test_downgrade_preserves_source_and_can_upgrade_again(self):
        before = self.conn.exec_driver_sql('SELECT chat::text FROM chat ORDER BY id').all()
        with Operations.context(MigrationContext.configure(self.conn)):
            load_migration().downgrade()
        self.assertEqual(self.conn.exec_driver_sql('SELECT chat::text FROM chat ORDER BY id').all(), before)
        self.assertNotIn('owui_search_messages', [c['name'] for c in sa.inspect(self.conn).get_columns('chat')])
        with Operations.context(MigrationContext.configure(self.conn)):
            load_migration().upgrade()
        self.assertEqual(
            self.conn.exec_driver_sql("SELECT owui_search_messages FROM chat WHERE id='backfill'").scalar(),
            ['historical 的'],
        )

    def test_extension_denial_keeps_required_projection_migration(self):
        # A managed database may allow schema changes but deny CREATE EXTENSION.
        # Exercise a real permission failure and savepoint, not a mocked exception.
        role = 'test_no_extension_' + uuid.uuid4().hex
        schema = 'test_no_extension_' + uuid.uuid4().hex
        self.conn.exec_driver_sql('DROP EXTENSION IF EXISTS pg_trgm CASCADE')
        self.conn.exec_driver_sql(f'CREATE ROLE {role}')
        self.conn.exec_driver_sql(f'CREATE SCHEMA {schema} AUTHORIZATION {role}')
        self.conn.exec_driver_sql(f'SET LOCAL ROLE {role}')
        self.conn.exec_driver_sql(f'SET LOCAL search_path TO {schema}, public')
        self.conn.exec_driver_sql('CREATE TABLE chat (chat json)')
        self.conn.exec_driver_sql('CREATE TABLE chat_message (content json)')
        with Operations.context(MigrationContext.configure(self.conn)):
            load_migration().upgrade()
        self.conn.exec_driver_sql('INSERT INTO chat_message(content) VALUES (\'"Works 的"\')')
        self.assertEqual(self.conn.exec_driver_sql('SELECT owui_search_text FROM chat_message').scalar(), 'works 的')
        self.assertEqual(sa.inspect(self.conn).get_indexes('chat_message'), [])
        self.conn.exec_driver_sql('RESET ROLE')

    def test_workspace_and_legacy_keep_their_different_fallback_rules(self):
        from open_webui.models.chats import chat_search_message_content_match_sql
        from open_webui.routers.search import message_match_sql

        self.conn.exec_driver_sql(
            """INSERT INTO chat VALUES ('partial','owner','{"messages":[{"content":"legacyonly"}]}');
            INSERT INTO chat_message VALUES ('partial-m','partial','owner','"normalizedonly"');
            INSERT INTO chat VALUES ('foreign','owner','{}');
            INSERT INTO chat_message VALUES ('foreign-m','foreign','someone-else','"privatevalue"')"""
        )
        for term in ['legacyonly', 'normalizedonly', 'privatevalue', '的', 'absent']:
            workspace = message_match_sql('chat', 'postgresql', 'q')
            actual = (
                self.conn.execute(sa.text(f'SELECT id FROM chat WHERE {workspace} ORDER BY id'), {'q': f'%{term}%'})
                .scalars()
                .all()
            )
            legacy = (
                self.conn.execute(
                    sa.text(
                        f'SELECT id FROM chat WHERE {chat_search_message_content_match_sql("postgresql", "q")} ORDER BY id'
                    ),
                    {'q': term},
                )
                .scalars()
                .all()
            )
            if term == 'legacyonly':
                self.assertEqual(actual, [])
                self.assertEqual(legacy, ['partial'])
            elif term == 'normalizedonly':
                self.assertEqual(actual, ['partial'])
                self.assertEqual(legacy, ['partial'])
            elif term == 'privatevalue':
                self.assertEqual(actual, ['foreign'])  # outer workspace query enforces chat access
                self.assertEqual(legacy, [])  # legacy additionally matches message ownership
            elif term == '的':
                self.assertEqual(actual, ['backfill'])
                self.assertEqual(legacy, ['backfill'])
            else:
                self.assertEqual(actual, [])
                self.assertEqual(legacy, [])


@unittest.skipUnless(os.environ.get('CHAT_SEARCH_TEST_DATABASE_URL'), 'disposable PostgreSQL URL required')
class WorkspaceBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from open_webui.internal.db import Base
        from open_webui.routers import search  # register the tables used by the real query

        cls.database_url = os.environ['CHAT_SEARCH_TEST_DATABASE_URL']
        cls.engine = sa.create_engine(cls.database_url)
        cls.schema = 'test_workspace_' + uuid.uuid4().hex
        with cls.engine.begin() as conn:
            if sa.inspect(conn).has_table('chat', schema='public'):
                raise RuntimeError('Refusing to test against a database with existing application tables')
            conn.exec_driver_sql(f'CREATE SCHEMA {cls.schema}')
            conn.exec_driver_sql(f'SET search_path TO {cls.schema}, public')
            Base.metadata.create_all(conn)
            with Operations.context(MigrationContext.configure(conn)):
                load_migration().upgrade()

    @classmethod
    def tearDownClass(cls):
        with cls.engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA {cls.schema} CASCADE')
        cls.engine.dispose()

    def test_real_search_permissions_filters_order_and_cross_message_terms(self):
        asyncio.run(self.exercise_workspace())

    async def exercise_workspace(self):
        from open_webui.internal import db as db_module
        from open_webui.models.access_grants import AccessGrant
        from open_webui.models.chat_messages import ChatMessage, ChatMessages
        from open_webui.models.chats import Chat, Chats
        from open_webui.models.folders import Folder
        from open_webui.models.shared_chats import SharedChat
        from open_webui.routers.search import search_workspace
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        url = sa.engine.make_url(self.database_url)
        postgres = url.get_backend_name() == 'postgresql'
        url = url.set(drivername='postgresql+psycopg' if postgres else 'sqlite+aiosqlite')
        engine = create_async_engine(
            url, connect_args={'options': f'-csearch_path={self.schema},public'} if postgres else {}
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            with (
                patch.object(db_module, 'DATABASE_ENABLE_SESSION_SHARING', True),
                patch.object(db_module, 'AsyncSessionLocal', sessions),
            ):
                async with sessions() as db:
                    user = SimpleNamespace(id='owner', role='user', name='Test')
                    for i in range(27):
                        db.add(
                            Chat(
                                id=f'own-{i:02}',
                                user_id='owner',
                                title='Alpha',
                                chat={},
                                created_at=1,
                                updated_at=i + 1,
                                archived=False,
                                meta={},
                            )
                        )
                    db.add(
                        Chat(
                            id='foreign',
                            user_id='other',
                            title='Alpha',
                            chat={},
                            created_at=1,
                            updated_at=100,
                            archived=False,
                            meta={},
                        )
                    )
                    db.add(
                        Chat(
                            id='archived',
                            user_id='owner',
                            title='Alpha',
                            chat={},
                            created_at=1,
                            updated_at=200,
                            archived=True,
                            meta={},
                        )
                    )
                    db.add(
                        Chat(
                            id='internal',
                            user_id='owner',
                            title='Alpha',
                            chat={},
                            created_at=1,
                            updated_at=300,
                            archived=False,
                            meta={'internal': True},
                        )
                    )
                    await db.flush()
                    # Use the application's bulk writer, not only direct SQL: generated
                    # columns must never be included in its INSERT payload.
                    await ChatMessages.upsert_messages(
                        'own-26',
                        'owner',
                        {
                            'a': {'role': 'user', 'content': 'Beta 100% a_b 配置的'},
                            'b': {'role': 'assistant', 'content': 'Gamma'},
                        },
                        db=db,
                    )
                    await db.commit()

                    async def search(query='', **kwargs):
                        return await search_workspace(db, user, query, kind='chat', enabled=('chat',), **kwargs)

                    first = await search('alpha')
                    second = await search('alpha', page=2)
                    self.assertEqual(len(first.items), 24)
                    self.assertTrue(first.has_more)
                    self.assertEqual([x.id for x in first.items[:2]], ['chat:own-26', 'chat:own-25'])
                    self.assertEqual(len(second.items), 3)
                    self.assertFalse(second.has_more)
                    for term in ['alpha beta gamma', '配置', '的', '100%', 'a_b']:
                        response = await search(term)
                        self.assertEqual([x.id for x in response.items], ['chat:own-26'], term)
                    self.assertEqual([x.id for x in (await search('alpha archived:true')).items], ['chat:archived'])
                    self.assertEqual((await search('alpha', sharing='shared')).items, [])
                    folder = Folder(id='shared-folder', user_id='other', name='Shared', created_at=1, updated_at=1)
                    db.add(folder)
                    foreign = await db.get(Chat, 'foreign')
                    foreign.folder_id = folder.id
                    db.add(
                        AccessGrant(
                            id='grant-folder',
                            resource_type='folder',
                            resource_id=folder.id,
                            principal_type='user',
                            principal_id='owner',
                            permission='read',
                            created_at=1,
                        )
                    )
                    await db.commit()
                    self.assertEqual(
                        [x.id for x in (await search('alpha', scope='folder:shared-folder')).items], ['chat:foreign']
                    )
                    await db.execute(sa.delete(AccessGrant).where(AccessGrant.id == 'grant-folder'))
                    db.add(
                        SharedChat(
                            id='snapshot',
                            chat_id='foreign',
                            user_id='other',
                            title='Snapshot',
                            chat={'messages': [{'content': 'snapshotword'}]},
                            created_at=1,
                            updated_at=1,
                        )
                    )
                    db.add(
                        AccessGrant(
                            id='grant-snapshot',
                            resource_type='shared_chat',
                            resource_id='foreign',
                            principal_type='user',
                            principal_id='owner',
                            permission='read',
                            created_at=1,
                        )
                    )
                    await db.commit()
                    self.assertEqual([x.id for x in (await search('snapshotword')).items], ['shared:snapshot'])
                    self.assertNotIn('chat:foreign', [x.id for x in (await search('alpha')).items])
                    await db.execute(sa.delete(AccessGrant).where(AccessGrant.id == 'grant-snapshot'))
                    await db.commit()
                    self.assertEqual((await search('snapshotword')).items, [])
                    if postgres:
                        # Protect the performance mechanism through the real candidate
                        # query, without a machine-dependent latency assertion.
                        await db.execute(
                            sa.insert(ChatMessage),
                            [
                                {
                                    'id': f'plan-{i}',
                                    'chat_id': 'own-26',
                                    'user_id': 'owner',
                                    'role': 'assistant',
                                    'content': 'padding ' * 30,
                                }
                                for i in range(2000)
                            ],
                        )
                        await db.execute(sa.text('ANALYZE chat_message'))
                        statements = []

                        def capture(conn, cursor, statement, parameters, context, many):
                            if 'UNION ALL' in statement and 'rank' in statement:
                                statements.append((statement, parameters))

                        sa.event.listen(engine.sync_engine, 'after_cursor_execute', capture)
                        try:
                            await db.execute(sa.text('SET LOCAL enable_seqscan=off'))
                            await search('indexprobeabsent')
                            connection = await db.connection()
                            explained = await connection.exec_driver_sql(
                                'EXPLAIN (FORMAT JSON) ' + statements[0][0], statements[0][1]
                            )
                            plan = explained.scalar_one()[0]['Plan']

                            def indexes(node):
                                yield node.get('Index Name')
                                for child in node.get('Plans', []):
                                    yield from indexes(child)

                            self.assertIn('ix_chat_message_search_trgm', list(indexes(plan)))
                            await db.execute(sa.text('SET LOCAL enable_seqscan=on'))
                        finally:
                            sa.event.remove(engine.sync_engine, 'after_cursor_execute', capture)
                    # The old API keeps its own contract, title order and filters.
                    old = await Chats.get_chats_by_user_id_and_search_text('owner', 'alpha beta gamma', db=db)
                    self.assertEqual([x.id for x in old], ['own-26'] if postgres else [])
                    await db.execute(sa.delete(ChatMessage).where(ChatMessage.chat_id == 'own-26'))
                    await db.commit()
                    self.assertEqual((await search('beta')).items, [])
        finally:
            await engine.dispose()


class SQLiteWorkspaceTest(WorkspaceBehaviorTest):
    __unittest_skip__ = False

    @classmethod
    def setUpClass(cls):
        from open_webui.internal.db import Base
        from open_webui.routers import search

        cls.tempdir = tempfile.TemporaryDirectory()
        cls.database_url = 'sqlite:///' + str(Path(cls.tempdir.name) / 'test.db')
        cls.engine = sa.create_engine(cls.database_url)
        with cls.engine.begin() as conn:
            Base.metadata.create_all(conn)
            with Operations.context(MigrationContext.configure(conn)):
                load_migration().upgrade()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.tempdir.cleanup()


if __name__ == '__main__':
    unittest.main()
