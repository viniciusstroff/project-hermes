import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

psycopg2 = types.ModuleType('psycopg2')
psycopg2_extras = types.ModuleType('psycopg2.extras')
psycopg2_extras.RealDictCursor = object
psycopg2.extras = psycopg2_extras
psycopg2.connect = lambda *args, **kwargs: None
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)

firebirdsql = types.ModuleType('firebirdsql')
firebirdsql.connect = lambda *args, **kwargs: None
sys.modules.setdefault('firebirdsql', firebirdsql)

dotenv = types.ModuleType('dotenv')


def dotenv_values(path):
    values = {}
    with open(path, encoding='utf-8') as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key] = value
    return values


dotenv.dotenv_values = dotenv_values
sys.modules.setdefault('dotenv', dotenv)

from core.adapters import PostgresSourceAdapter, PostgresTargetAdapter
from core.context import DefaultMigrationContextFactory
from core.metadata import PostgresMetadataProvider
from core.runner import MigrationRunner
from core.sql import SqlLiteral


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed_sql = None
        self.closed = False
        self.itersize = None

    def execute(self, sql):
        self.executed_sql = sql

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.cursor_calls = []
        self.closed = False

    def cursor(self, *args, **kwargs):
        cursor = FakeCursor(self.rows)
        self.cursor_calls.append((args, kwargs, cursor))
        return cursor

    def close(self):
        self.closed = True


class AdapterContextMetadataTest(unittest.TestCase):
    def _write_env(self, clientdir):
        with open(os.path.join(clientdir, '.env'), 'w', encoding='utf-8') as env_file:
            env_file.write(
                '\n'.join([
                    'PG_V1_HOST=source-db',
                    'PG_V1_PORT=5432',
                    'PG_V1_NAME=source',
                    'PG_V1_USER=source-user',
                    'PG_V1_PASS=source-pass',
                    'PG_V2_HOST=target-db',
                    'PG_V2_PORT=5432',
                    'PG_V2_NAME=target',
                    'PG_V2_USER=target-user',
                    'PG_V2_PASS=target-pass',
                    'FB_V1_HOST=firebird-db',
                    'FB_V1_PORT=3050',
                    'FB_V1_NAME=firebird',
                    'FB_V1_USER=firebird-user',
                    'FB_V1_PASS=firebird-pass',
                    'FB_V1_CHARSET=ISO8859_1',
                ])
            )

    def test_postgres_source_adapter_owns_cursor_and_scalar_behavior(self):
        conn = FakeConnection(rows=[{'count': 7}])
        adapter = PostgresSourceAdapter(conn, cursor_factory=object, cursor_prefix='test', itersize=123)

        cursor = adapter.cursor()
        scalar = adapter.scalar('SELECT COUNT(*) FROM source')

        self.assertTrue(conn.cursor_calls[0][0][0].startswith('test_'))
        self.assertIs(conn.cursor_calls[0][1]['cursor_factory'], object)
        self.assertEqual(cursor.itersize, 123)
        self.assertEqual(conn.cursor_calls[1][2].executed_sql, 'SELECT COUNT(*) FROM source')
        self.assertTrue(conn.cursor_calls[1][2].closed)
        self.assertEqual(scalar, 7)

    def test_target_adapter_preserves_rendered_sql_literals(self):
        adapter = PostgresTargetAdapter()

        sql = adapter.insert_statement(
            'public.demo',
            ['f_raw', 'f_name'],
            [SqlLiteral('CURRENT_TIMESTAMP'), "O'Brien"],
        )

        self.assertEqual(
            sql,
            "INSERT INTO public.demo (f_raw, f_name) VALUES (CURRENT_TIMESTAMP, 'O''Brien');\n",
        )

    def test_metadata_provider_loads_maps_and_renders_helpers(self):
        conn = FakeConnection(rows=[(' A ', 1), (None, 2)])
        provider = PostgresMetadataProvider(conn)

        ref_map = provider.load_reference_map('SELECT key, value FROM refs')

        self.assertEqual(ref_map, {'A': 1})
        self.assertEqual(conn.cursor_calls[0][2].executed_sql, 'SELECT key, value FROM refs')
        self.assertIn('indisready=false', provider.disable_indexes_sql(['idx_demo']))
        self.assertIn('idx_demo', provider.enable_indexes_sql(['idx_demo']))
        self.assertIn("setval('demo_f_id_seq'", provider.reset_sequence_sql('demo'))
        self.assertIn("setval('demo_f_id_seq'", provider.reset_sequences_sql(['demo']))

    def test_default_context_factory_builds_composed_runtime_from_existing_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)
            self._write_env(clientdir)
            pg_v1 = FakeConnection()
            pg_v2 = FakeConnection()
            fb_v1 = FakeConnection()

            with patch('core.context.psycopg2.connect', side_effect=[pg_v1, pg_v2]), \
                 patch('core.context.firebirdsql.connect', return_value=fb_v1):
                with redirect_stdout(StringIO()):
                    context = DefaultMigrationContextFactory.from_clientdir(clientdir)

            self.assertIn('pg_v1', context.sources)
            self.assertIn('fb_v1', context.sources)
            self.assertIs(context.legacy_connections['pg_v1_conn'], pg_v1)
            self.assertIs(context.legacy_connections['pg_v2_conn'], pg_v2)
            self.assertIs(context.legacy_connections['fb_v1_conn'], fb_v1)
            self.assertIsInstance(context.target, PostgresTargetAdapter)
            self.assertIsInstance(context.metadata, PostgresMetadataProvider)

            engine = context.engine('pg_v1')
            self.assertIs(engine.source_adapter, context.sources['pg_v1'])
            self.assertIs(engine.target_adapter, context.target)

            context.close()
            self.assertTrue(pg_v1.closed)
            self.assertTrue(pg_v2.closed)
            self.assertTrue(fb_v1.closed)

    def test_runner_accepts_composed_migration_without_base_inheritance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)
            self._write_env(clientdir)
            pg_v1 = FakeConnection()
            pg_v2 = FakeConnection(rows=[
                ('ALTER TABLE public.demo DISABLE TRIGGER ALL;', 'ALTER TABLE public.demo ENABLE TRIGGER ALL;'),
            ])
            fb_v1 = FakeConnection()

            class ComposedMigration:
                def __init__(self, context):
                    self.context = context
                    self.enable_indexes = ''

                def run_initial(self):
                    self.context.writer.write_sql('-- prepare\n')

                def run_inserts(self):
                    self.context.writer.write_sql('-- load\n')

                def reset_sequences(self):
                    self.context.writer.write_sql(self.context.metadata.reset_sequence_sql('demo'))

            with patch('core.context.psycopg2.connect', side_effect=[pg_v1, pg_v2]), \
                 patch('core.context.firebirdsql.connect', return_value=fb_v1):
                with redirect_stdout(StringIO()):
                    context = DefaultMigrationContextFactory.from_clientdir(clientdir)
                    MigrationRunner(context, ComposedMigration(context)).run()

            context.close()
            with open(context.artifacts.sql_path('prepare_target'), encoding='utf-8') as sql_file:
                self.assertEqual(sql_file.read(), '-- prepare\n')
            with open(context.artifacts.sql_path('load_transformed_data'), encoding='utf-8') as sql_file:
                self.assertEqual(sql_file.read(), '-- load\n')
            with open(context.artifacts.sql_path('finalize_target'), encoding='utf-8') as sql_file:
                finalize_sql = sql_file.read()

            self.assertIn("setval('demo_f_id_seq'", finalize_sql)
            self.assertIn('ALTER TABLE public.demo ENABLE TRIGGER ALL;', finalize_sql)


if __name__ == '__main__':
    unittest.main()
