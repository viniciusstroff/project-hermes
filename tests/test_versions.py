import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# Mesmos stubs dos outros testes: o primeiro módulo importado na descoberta
# define o stub de todos, então ele precisa servir para todos.
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

from core.adapters.firebird import FirebirdSourceAdapter
from core.adapters.postgres import PostgresSourceAdapter, PostgresTargetAdapter
from core.apply import ArtifactApplier
from core.connections import ConnectionConfig, ConnectionConfigError
from core.versions import UnsupportedVersionError, check_server_version, parse_version, postgres_version
from tests.apply_fakes import COPY, DEFAULT_FILES, FakeConnection, make_run, session_of

POSTGRES = {'host': 'db', 'name': 'base', 'user': 'carga'}


def postgres_config(prefix='SOURCE_V1'):
    return ConnectionConfig(prefix, prefix.lower(), 'postgres', POSTGRES)


class VersionParsingTest(unittest.TestCase):
    def test_parse_and_postgres_numbers(self):
        self.assertEqual(parse_version('3.0.10'), (3, 0, 10))
        self.assertEqual(parse_version('WI-V3.0.10.33601 Firebird 3.0'), (3, 0, 10, 33601))
        self.assertIsNone(parse_version(None))
        self.assertEqual(postgres_version(90624), (9, 6, 24))
        self.assertEqual(postgres_version(160004), (16, 4))
        self.assertIsNone(postgres_version(None))

    def test_check_names_adapter_role_version_and_minimum(self):
        check_server_version('SOURCE_V1', 'postgres', 'source', (9, 6, 0), (9, 6))
        check_server_version('SOURCE_V1', 'postgres', 'source', None, (9, 6))

        with self.assertRaises(UnsupportedVersionError) as raised:
            check_server_version('TARGET', 'postgres', 'target', (11, 22), (12,))

        message = str(raised.exception)
        self.assertIn('TARGET', message)
        self.assertIn('postgres 11.22', message)
        self.assertIn('destino a partir da versão 12', message)
        self.assertIsInstance(raised.exception, ConnectionConfigError)


class PostgresRangeTest(unittest.TestCase):
    def _connection(self, number):
        connection = FakeConnection()
        connection.server_version = number
        return connection

    def test_source_from_96_and_target_from_12(self):
        cases = [
            (PostgresSourceAdapter, 'SOURCE_V1', 90624, None),
            (PostgresSourceAdapter, 'SOURCE_V1', 90426, 'origem a partir da versão 9.6'),
            (PostgresTargetAdapter, 'TARGET', 120000, None),
            (PostgresTargetAdapter, 'TARGET', 110022, 'destino a partir da versão 12'),
        ]
        for adapter_class, prefix, number, refusal in cases:
            with self.subTest(adapter=adapter_class.__name__, number=number):
                connection = self._connection(number)
                with patch('psycopg2.connect', return_value=connection):
                    if refusal is None:
                        adapter = adapter_class.connect(postgres_config(prefix))
                        self.assertEqual(adapter.server_version, '.'.join(map(str, postgres_version(number))))
                        self.assertFalse(connection.closed)
                    else:
                        with self.assertRaises(UnsupportedVersionError) as raised:
                            adapter_class.connect(postgres_config(prefix))
                        self.assertIn(refusal, str(raised.exception))
                        self.assertTrue(connection.closed)


class FakeFirebirdConnection:
    def __init__(self, version=None, error=None):
        self.version = version
        self.error = error
        self.closed = False

    def cursor(self):
        connection = self

        class Cursor:
            def execute(self, sql):
                if connection.error:
                    raise connection.error
                self.sql = sql

            def fetchone(self):
                return (connection.version,)

            def close(self):
                pass

        return Cursor()

    def close(self):
        self.closed = True


class FirebirdRangeTest(unittest.TestCase):
    CONFIG = ConnectionConfig('SOURCE_FB', 'fb', 'firebird', POSTGRES)

    def test_reads_engine_version_and_accepts_from_25(self):
        for version in ('2.5.9', '3.0.10', '5.0.0'):
            with self.subTest(version=version), patch('firebirdsql.connect', return_value=FakeFirebirdConnection(version)):
                self.assertEqual(FirebirdSourceAdapter.connect(self.CONFIG).server_version, version)

    def test_older_server_is_refused_and_closed(self):
        connection = FakeFirebirdConnection('2.1.7')

        with patch('firebirdsql.connect', return_value=connection), self.assertRaises(UnsupportedVersionError) as raised:
            FirebirdSourceAdapter.connect(self.CONFIG)

        self.assertIn('firebird 2.1.7', str(raised.exception))
        self.assertIn('2.5', str(raised.exception))
        self.assertTrue(connection.closed)

    def test_server_without_engine_version_is_refused(self):
        connection = FakeFirebirdConnection(error=RuntimeError('Function unknown RDB$GET_CONTEXT'))

        with patch('firebirdsql.connect', return_value=connection), self.assertRaises(UnsupportedVersionError) as raised:
            FirebirdSourceAdapter.connect(self.CONFIG)

        self.assertIn('SOURCE_FB', str(raised.exception))
        self.assertTrue(connection.closed)


class DumpOfNewerServerTest(unittest.TestCase):
    def test_settings_the_target_does_not_know_are_skipped_and_counted(self):
        files = dict(DEFAULT_FILES)
        files[COPY] = (
            'SET statement_timeout = 0;\n'
            'SET transaction_timeout = 0;\n'
            "SELECT pg_catalog.set_config('search_path', '', false);\n"
            "SELECT pg_catalog.set_config('futuro.parametro', 'x', false);\n"
            "SET TIME ZONE 'UTC';\n"
            'INSERT INTO public.a VALUES (1);\n'
        )
        connection = FakeConnection()
        with tempfile.TemporaryDirectory() as tmpdir:
            report = ArtifactApplier(make_run(tmpdir, files), session_of(connection)).apply()

        copy = report.as_dict()['files'][2]
        self.assertEqual(copy['skipped'], {'unknown_setting': 2})
        self.assertEqual(copy['executed'], 4)
        self.assertNotIn('SET transaction_timeout = 0', connection.log)
        self.assertIn('SET statement_timeout = 0', connection.log)
        self.assertIn("SET TIME ZONE 'UTC'", connection.log)


if __name__ == '__main__':
    unittest.main()
