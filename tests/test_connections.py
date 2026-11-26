import os
import subprocess
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

from core.adapters.firebird import FirebirdSourceAdapter
from core.adapters.postgres import PostgresSourceAdapter, PostgresTargetAdapter
from core.adapters.registry import AdapterResolutionError, connect_adapter, resolve_adapter
from core.connections import ConnectionConfig, ConnectionConfigError, parse_connections
from core.context import DefaultMigrationContextFactory

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGET_KEYS = {
    'TARGET_ADAPTER': 'postgres',
    'TARGET_HOST': 'target-db',
    'TARGET_NAME': 'tenant',
    'TARGET_USER': 'carga',
    'TARGET_PASS': 'segredo',
}


class CompleteTarget:
    connected_with = None

    @classmethod
    def connect(cls, config):
        cls.connected_with = config
        return cls()

    def render(self, operation):
        return []

    def read(self, query):
        return iter(())

    def close(self):
        pass


class TargetWithoutRender:
    """Target written for the old contract: insert_statement() instead of render()."""

    @classmethod
    def connect(cls, config):
        return cls()

    def insert_statement(self, table, columns, values):
        return ''

    def read(self, query):
        return iter(())

    def close(self):
        pass


class ParseConnectionsTest(unittest.TestCase):
    def test_groups_sources_by_name_and_target(self):
        sources, target = parse_connections({
            'SOURCE_V1_ADAPTER': 'postgres',
            'SOURCE_V1_HOST': 'acmeedu-v1',
            'SOURCE_V1_PORT': '5432',
            'SOURCE_LEGADO_ADAPTER': 'firebird',
            'SOURCE_LEGADO_CHARSET': 'ISO8859_1',
            **TARGET_KEYS,
        })

        self.assertEqual(sorted(sources), ['legado', 'v1'])
        self.assertEqual(sources['v1'].adapter, 'postgres')
        self.assertEqual(sources['v1'].prefix, 'SOURCE_V1')
        self.assertEqual(dict(sources['v1'].params), {'host': 'acmeedu-v1', 'port': '5432'})
        self.assertEqual(dict(sources['legado'].params), {'charset': 'ISO8859_1'})
        self.assertEqual(target.adapter, 'postgres')
        self.assertEqual(target.get('host'), 'target-db')
        self.assertNotIn('adapter', target.params)

    def test_longest_declared_name_owns_the_key(self):
        sources, _ = parse_connections({
            'SOURCE_V1_ADAPTER': 'postgres',
            'SOURCE_V1_HOST': 'novo',
            'SOURCE_V1_LEGACY_ADAPTER': 'firebird',
            'SOURCE_V1_LEGACY_HOST': 'antigo',
            **TARGET_KEYS,
        })

        self.assertEqual(sources['v1'].get('host'), 'novo')
        self.assertEqual(sources['v1_legacy'].get('host'), 'antigo')
        self.assertNotIn('legacy_host', sources['v1'].params)

    def test_key_without_declared_source_is_refused(self):
        with self.assertRaises(ConnectionConfigError) as raised:
            parse_connections({'SOURCE_V1_ADAPTER': 'postgres', 'SOURCE_V2_HOST': 'db', **TARGET_KEYS})

        self.assertIn('SOURCE_V2_HOST', str(raised.exception))

    def test_requires_at_least_one_source(self):
        with self.assertRaises(ConnectionConfigError) as raised:
            parse_connections({**TARGET_KEYS, 'PG_V1_HOST': 'db'})

        self.assertIn('SOURCE_<NOME>_ADAPTER', str(raised.exception))

    def test_empty_source_adapter_is_refused(self):
        with self.assertRaises(ConnectionConfigError) as raised:
            parse_connections({'SOURCE_V1_ADAPTER': ' ', **TARGET_KEYS})

        self.assertIn('SOURCE_V1_ADAPTER', str(raised.exception))

    def test_requires_target_adapter(self):
        with self.assertRaises(ConnectionConfigError) as raised:
            parse_connections({'SOURCE_V1_ADAPTER': 'postgres', 'TARGET_HOST': 'db'})

        self.assertIn('TARGET_ADAPTER', str(raised.exception))


class ConnectionConfigTest(unittest.TestCase):
    def test_require_names_the_missing_env_keys(self):
        config = ConnectionConfig('SOURCE_V1', 'v1', 'postgres', {'host': 'db', 'name': ''})

        with self.assertRaises(ConnectionConfigError) as raised:
            config.require('host', 'name', 'user')

        message = str(raised.exception)
        self.assertIn('SOURCE_V1_NAME', message)
        self.assertIn('SOURCE_V1_USER', message)
        self.assertNotIn('SOURCE_V1_HOST', message)

    def test_empty_value_falls_back_to_default(self):
        config = ConnectionConfig('TARGET', 'target', 'postgres', {'port': ''})

        self.assertEqual(config.get('port', '5432'), '5432')

    def test_describe_omits_secrets(self):
        config = ConnectionConfig('SOURCE_DOCS', 'docs', 'mongodb', {
            'uri': 'mongodb://user:segredo@db',
            'pass': 'segredo',
            'password': 'segredo',
            'database': 'docs',
        })

        self.assertEqual(config.describe(), {'adapter': 'mongodb', 'database': 'docs'})


class RegistryTest(unittest.TestCase):
    def test_builtin_names(self):
        self.assertIs(resolve_adapter('postgres', 'source'), PostgresSourceAdapter)
        self.assertIs(resolve_adapter('postgres', 'target'), PostgresTargetAdapter)
        self.assertIs(resolve_adapter('firebird', 'source'), FirebirdSourceAdapter)

    def test_unknown_name_lists_builtins(self):
        with self.assertRaises(AdapterResolutionError) as raised:
            resolve_adapter('oracle', 'source')

        self.assertIn("'oracle'", str(raised.exception))
        self.assertIn('firebird, mongodb, postgres', str(raised.exception))

    def test_adapter_without_the_role(self):
        with self.assertRaises(AdapterResolutionError) as raised:
            resolve_adapter('firebird', 'target')

        self.assertIn("'firebird' não serve como destino", str(raised.exception))

    def test_third_party_path(self):
        config = ConnectionConfig('TARGET', 'target', f'{__name__}:CompleteTarget', {})

        adapter = connect_adapter(config, 'target')

        self.assertIsInstance(adapter, CompleteTarget)
        self.assertIs(CompleteTarget.connected_with, config)

    def test_third_party_path_missing_contract_method(self):
        with self.assertRaises(AdapterResolutionError) as raised:
            resolve_adapter(f'{__name__}:TargetWithoutRender', 'target')

        self.assertIn('render', str(raised.exception))

    def test_third_party_path_that_cannot_be_imported(self):
        with self.assertRaises(AdapterResolutionError) as raised:
            resolve_adapter('hermes_extra.cassandra:CassandraTarget', 'target')

        self.assertIn('hermes_extra.cassandra:CassandraTarget', str(raised.exception))


class DriversOnDemandTest(unittest.TestCase):
    def test_core_imports_without_database_drivers(self):
        script = '\n'.join([
            'import sys, types',
            "for name in ('psycopg2', 'psycopg2.extras', 'firebirdsql', 'pymongo', 'bson'):",
            '    sys.modules[name] = None',
            "dotenv = types.ModuleType('dotenv')",
            'dotenv.dotenv_values = lambda path: {}',
            "sys.modules['dotenv'] = dotenv",
            'import core, core.context, core.base_migration, core.adapters.registry, Migracao, Aplicar',
            'from core.adapters.registry import resolve_adapter',
            "resolve_adapter('postgres', 'source')",
            "resolve_adapter('postgres', 'target')",
            "resolve_adapter('firebird', 'source')",
            "resolve_adapter('mongodb', 'source')",
            "resolve_adapter('mongodb', 'target')",
        ])

        result = subprocess.run(
            [sys.executable, '-c', script], cwd=PROJECT_ROOT, capture_output=True, text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_postgres_only_client_does_not_need_firebird_driver(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)
            with open(os.path.join(clientdir, '.env'), 'w', encoding='utf-8') as env_file:
                env_file.write('\n'.join([
                    'SOURCE_V1_ADAPTER=postgres',
                    'SOURCE_V1_HOST=source-db',
                    'SOURCE_V1_NAME=source',
                    'SOURCE_V1_USER=source-user',
                    *(f'{key}={value}' for key, value in TARGET_KEYS.items()),
                ]))

            with patch.dict(sys.modules, {'firebirdsql': None, 'pymongo': None}), \
                 patch('psycopg2.connect', side_effect=lambda **kwargs: types.SimpleNamespace(close=lambda: None)):
                with redirect_stdout(StringIO()):
                    context = DefaultMigrationContextFactory.from_clientdir(clientdir)
            context.close()

        self.assertEqual(list(context.sources), ['v1'])

    def test_missing_required_parameter_stops_before_connecting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)
            with open(os.path.join(clientdir, '.env'), 'w', encoding='utf-8') as env_file:
                env_file.write('\n'.join([
                    'SOURCE_V1_ADAPTER=postgres',
                    'SOURCE_V1_HOST=source-db',
                    *(f'{key}={value}' for key, value in TARGET_KEYS.items()),
                ]))

            with patch('psycopg2.connect') as connect:
                with redirect_stdout(StringIO()), self.assertRaises(ConnectionConfigError) as raised:
                    DefaultMigrationContextFactory.from_clientdir(clientdir)

        connect.assert_not_called()
        self.assertIn('SOURCE_V1_NAME', str(raised.exception))
        self.assertIn('SOURCE_V1_USER', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
