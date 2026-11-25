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
from core.context import DefaultMigrationContextFactory, SourceNotConfiguredError
from core.operations import (
    Clear,
    Comment,
    Insert,
    Native,
    ResetIdentity,
    ResumeConstraints,
    SuspendConstraints,
    ValueSerializationError,
)
from core.queries import NativeAdapterMismatchError, NativeQuery, SourceQuery
from core.references import ReferenceMap, TargetReadClosedError, reference_pairs, reference_query
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

    def rollback(self):
        self.rollback_calls = getattr(self, 'rollback_calls', 0) + 1


POSTGRES_ENV = [
    'SOURCE_V1_ADAPTER=postgres',
    'SOURCE_V1_HOST=source-db',
    'SOURCE_V1_PORT=5432',
    'SOURCE_V1_NAME=source',
    'SOURCE_V1_USER=source-user',
    'SOURCE_V1_PASS=source-pass',
    'TARGET_ADAPTER=postgres',
    'TARGET_HOST=target-db',
    'TARGET_PORT=5432',
    'TARGET_NAME=target',
    'TARGET_USER=target-user',
    'TARGET_PASS=target-pass',
]
FIREBIRD_ENV = [
    'SOURCE_FB_ADAPTER=firebird',
    'SOURCE_FB_HOST=firebird-db',
    'SOURCE_FB_PORT=3050',
    'SOURCE_FB_NAME=firebird',
    'SOURCE_FB_USER=firebird-user',
    'SOURCE_FB_PASS=firebird-pass',
    'SOURCE_FB_CHARSET=ISO8859_1',
]


class AdapterContextMetadataTest(unittest.TestCase):
    def _write_env(self, clientdir, lines=None):
        with open(os.path.join(clientdir, '.env'), 'w', encoding='utf-8') as env_file:
            env_file.write('\n'.join(lines if lines is not None else [*POSTGRES_ENV, *FIREBIRD_ENV]))

    def _context_without_firebird(self, tmpdir, extra_lines=()):
        clientdir = os.path.join(tmpdir, 'AcmeEdu')
        os.makedirs(clientdir)
        self._write_env(clientdir, [*POSTGRES_ENV, *extra_lines])
        pg_v1 = FakeConnection()
        pg_v2 = FakeConnection()
        with patch('psycopg2.connect', side_effect=[pg_v1, pg_v2]), \
             patch('firebirdsql.connect') as firebird_connect:
            with redirect_stdout(StringIO()):
                context = DefaultMigrationContextFactory.from_clientdir(clientdir)
        return context, firebird_connect

    def test_context_without_firebird_source_does_not_connect_to_firebird(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            context, firebird_connect = self._context_without_firebird(tmpdir)

            firebird_connect.assert_not_called()
            self.assertEqual(list(context.sources), ['v1'])
            self.assertFalse(hasattr(context, 'legacy_connections'))
            context.close()

    def test_missing_source_names_the_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            context, _ = self._context_without_firebird(tmpdir)

            with self.assertRaises(SourceNotConfiguredError) as raised:
                context.source('fb')

            self.assertIn("'fb'", str(raised.exception))
            self.assertIn('v1', str(raised.exception))
            self.assertFalse(hasattr(context, 'engine'))
            context.close()

    def test_postgres_source_adapter_streams_with_named_cursor_and_counts(self):
        conn = FakeConnection(rows=[{'count': 7}])
        adapter = PostgresSourceAdapter(conn, cursor_factory=object, cursor_prefix='test', itersize=123)
        query = SourceQuery('public.source', fields=['f_id'], where={'f_active': True, 'f_deleted': None},
                            order_by='f_id', limit=5)
        select = 'SELECT f_id FROM public.source WHERE f_active = true AND f_deleted IS NULL ORDER BY f_id LIMIT 5'

        total = adapter.count(query)
        rows = list(adapter.read(query))

        count_cursor = conn.cursor_calls[0][2]
        read_args, read_kwargs, read_cursor = conn.cursor_calls[1]
        self.assertEqual(total, 7)
        self.assertEqual(count_cursor.executed_sql, f'SELECT COUNT(*) FROM ({select}) progress_src')
        self.assertTrue(count_cursor.closed)
        self.assertTrue(read_args[0].startswith('test_'))
        self.assertIs(read_kwargs['cursor_factory'], object)
        self.assertEqual(read_cursor.itersize, 123)
        self.assertEqual(read_cursor.executed_sql, select)
        self.assertTrue(read_cursor.closed)
        self.assertEqual(rows, [{'count': 7}])

    def test_target_adapter_renders_inserts_with_quoted_identifiers(self):
        adapter = PostgresTargetAdapter()

        commands = adapter.render(Insert('public.Pedidos', {
            'f_raw': SqlLiteral('CURRENT_TIMESTAMP'),
            'f_name': "O'Brien",
            'user': None,
        }))

        self.assertEqual(commands, [
            'INSERT INTO public."Pedidos" (f_raw, f_name, "user") '
            "VALUES (CURRENT_TIMESTAMP, 'O''Brien', NULL);\n",
        ])

    def test_target_adapter_names_the_column_of_a_value_it_cannot_store(self):
        with self.assertRaises(ValueSerializationError) as raised:
            PostgresTargetAdapter().render(Insert('public.notes', {'f_id': 1, 'f_tags': ['a']}))

        self.assertEqual((raised.exception.table, raised.exception.column), ('public.notes', 'f_tags'))

    def test_target_adapter_renders_maintenance_operations(self):
        adapter = PostgresTargetAdapter()

        self.assertEqual(
            adapter.render(Clear(['public.b', 'public.a'])),
            ['TRUNCATE public.b, public.a RESTART IDENTITY;\n'],
        )
        self.assertEqual(
            adapter.render(SuspendConstraints(['public.a', 'public.Pedidos'])),
            ['ALTER TABLE public.a DISABLE TRIGGER ALL;\n', 'ALTER TABLE public."Pedidos" DISABLE TRIGGER ALL;\n'],
        )
        self.assertEqual(
            adapter.render(ResumeConstraints(['public.a'])),
            ['ALTER TABLE public.a ENABLE TRIGGER ALL;\n'],
        )
        self.assertEqual(
            adapter.render(ResetIdentity('public.Pedidos', 'Id')),
            ["SELECT pg_catalog.setval(pg_catalog.pg_get_serial_sequence('public.\"Pedidos\"', 'Id'), "
             'COALESCE((SELECT MAX("Id") FROM public."Pedidos"), 1));\n'],
        )
        self.assertEqual(adapter.render(Native('postgres', 'VACUUM')), ['VACUUM\n'])
        self.assertEqual(adapter.render(Comment('SECAO')), ['\n----------\n-----SECAO\n----------\n\n'])

    def test_target_adapter_refuses_native_operation_of_another_adapter(self):
        with self.assertRaises(NativeAdapterMismatchError) as raised:
            PostgresTargetAdapter().render(Native('mongodb', {'delete': 'people'}))

        self.assertIn("'mongodb'", str(raised.exception))
        self.assertIn("'postgres'", str(raised.exception))

    def test_suspend_without_entities_reads_the_catalog_once(self):
        conn = FakeConnection(rows=[('public', 'a'), ('vendas', 'Pedidos')])
        adapter = PostgresTargetAdapter(connection=conn)

        disable = adapter.render(SuspendConstraints())
        enable = adapter.render(ResumeConstraints())

        self.assertEqual(disable, [
            'ALTER TABLE public.a DISABLE TRIGGER ALL;\n',
            'ALTER TABLE vendas."Pedidos" DISABLE TRIGGER ALL;\n',
        ])
        self.assertEqual(enable[1], 'ALTER TABLE vendas."Pedidos" ENABLE TRIGGER ALL;\n')
        self.assertEqual(len(conn.cursor_calls), 1)
        self.assertIn('pg_catalog.pg_tables', conn.cursor_calls[0][2].executed_sql)
        self.assertTrue(conn.cursor_calls[0][2].closed)

    def test_reference_pairs_from_table_declaration(self):
        conn = FakeConnection(rows=[{'f_code': ' A ', 'f_id': 1}, {'f_code': None, 'f_id': 2}])
        adapter = PostgresTargetAdapter(connection=conn)
        declaration = ReferenceMap(
            'states_by_code',
            table='public.states',
            key='f_code',
            value='f_id',
            filters={'f_active': True, 'Tipo': "O'B", 'f_deleted': None},
        )

        pairs = reference_pairs(declaration, adapter.read(reference_query(declaration)))

        self.assertEqual(pairs, [(' A ', 1), (None, 2)])
        self.assertEqual(
            conn.cursor_calls[0][2].executed_sql,
            'SELECT f_code, f_id FROM public.states '
            'WHERE f_active = true AND "Tipo" = \'O\'\'B\' AND f_deleted IS NULL',
        )

    def test_reference_pairs_from_query_declaration(self):
        conn = FakeConnection(rows=[{'code': 'SP', 'id': 25}])
        adapter = PostgresTargetAdapter(connection=conn)
        query = 'SELECT s.f_code, s.f_id FROM public.states s JOIN public.countries c ON c.f_id = s.f_country'
        declaration = ReferenceMap('states', query=NativeQuery('postgres', query))

        pairs = reference_pairs(declaration, adapter.read(reference_query(declaration)))

        self.assertEqual(pairs, [('SP', 25)])
        self.assertEqual(conn.cursor_calls[0][2].executed_sql, query)

    def test_close_reads_caches_the_catalog_and_refuses_later_reads(self):
        conn = FakeConnection(rows=[('public', 'a')])
        adapter = PostgresTargetAdapter(connection=conn)

        adapter.close_reads()

        self.assertEqual(conn.rollback_calls, 1)
        self.assertEqual(adapter.render(ResumeConstraints()), ['ALTER TABLE public.a ENABLE TRIGGER ALL;\n'])
        self.assertEqual(len(conn.cursor_calls), 1)
        with self.assertRaises(TargetReadClosedError):
            adapter.read(SourceQuery('public.x'))
        without_connection = PostgresTargetAdapter()
        without_connection.close_reads()
        with self.assertRaises(TargetReadClosedError):
            without_connection.render(SuspendConstraints())

    def test_server_version_from_the_connection(self):
        for number, text in ((90624, '9.6.24'), (160004, '16.4'), (170002, '17.2')):
            conn = FakeConnection()
            conn.server_version = number
            with self.subTest(number=number):
                self.assertEqual(PostgresTargetAdapter(connection=conn).server_version, text)
                self.assertEqual(PostgresSourceAdapter(conn, cursor_factory=object).server_version, text)
        self.assertIsNone(PostgresTargetAdapter().server_version)

    def test_default_context_factory_builds_composed_runtime_from_existing_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)
            self._write_env(clientdir)
            pg_v1 = FakeConnection()
            pg_v2 = FakeConnection()
            fb_v1 = FakeConnection()

            with patch('psycopg2.connect', side_effect=[pg_v1, pg_v2]), \
                 patch('firebirdsql.connect', return_value=fb_v1):
                with redirect_stdout(StringIO()):
                    context = DefaultMigrationContextFactory.from_clientdir(clientdir)

            self.assertEqual(sorted(context.sources), ['fb', 'v1'])
            self.assertIs(context.sources['v1'].connection, pg_v1)
            self.assertIs(context.sources['fb'].connection, fb_v1)
            self.assertIs(context.target.connection, pg_v2)
            self.assertIsInstance(context.target, PostgresTargetAdapter)
            self.assertFalse(hasattr(context, 'metadata'))

            engine = context.source('v1')
            self.assertIs(engine.source_adapter, context.sources['v1'])
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
            pg_v2 = FakeConnection(rows=[('public', 'demo')])
            fb_v1 = FakeConnection()

            class ComposedMigration:
                def __init__(self, context):
                    self.context = context

                def run_initial(self):
                    self.context.emit(SuspendConstraints())
                    self.context.emit(Native('postgres', '-- prepare\n'))

                def run_inserts(self):
                    self.context.emit(Native('postgres', '-- load\n'))

                def reset_sequences(self):
                    self.context.emit(ResetIdentity('public.demo', 'f_id'))

            with patch('psycopg2.connect', side_effect=[pg_v1, pg_v2]), \
                 patch('firebirdsql.connect', return_value=fb_v1):
                with redirect_stdout(StringIO()):
                    context = DefaultMigrationContextFactory.from_clientdir(clientdir)
                    MigrationRunner(context, ComposedMigration(context)).run()

            context.close()
            with open(context.artifacts.command_path('prepare_target', 'sql'), encoding='utf-8') as sql_file:
                self.assertEqual(sql_file.read(), 'ALTER TABLE public.demo DISABLE TRIGGER ALL;\n-- prepare\n')
            with open(context.artifacts.command_path('load_transformed_data', 'sql'), encoding='utf-8') as sql_file:
                self.assertEqual(sql_file.read(), '-- load\n')
            with open(context.artifacts.command_path('finalize_target', 'sql'), encoding='utf-8') as sql_file:
                finalize_sql = sql_file.read()

            self.assertIn("pg_get_serial_sequence('public.demo', 'f_id')", finalize_sql)
            self.assertIn('ALTER TABLE public.demo ENABLE TRIGGER ALL;', finalize_sql)


if __name__ == '__main__':
    unittest.main()
