import os
import json
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
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)
sys.modules['psycopg2'].connect = lambda *args, **kwargs: None
sys.modules['psycopg2'].extras = sys.modules['psycopg2.extras']

firebirdsql = types.ModuleType('firebirdsql')
sys.modules.setdefault('firebirdsql', firebirdsql)
sys.modules['firebirdsql'].connect = lambda *args, **kwargs: None

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

from core.base_migration import BaseMigration


class FakeCursor:
    def execute(self, sql):
        self.executed_sql = sql

    def __iter__(self):
        return iter([])


class FakeConnection:
    def cursor(self, *args, **kwargs):
        return FakeCursor()

    def close(self):
        pass


class ArtifactMigration(BaseMigration):
    def run_initial(self):
        self.write_sql('-- prepare\n')

    def run_inserts(self):
        self.write_sql('-- load\n')

    def reset_sequences(self):
        self.write_sql('-- finalize\n')

    def dump_ignoring_tables(self):
        return ['public.acme_ignore_demo']


class BaseMigrationArtifactsTest(unittest.TestCase):
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

    def test_uses_centralized_artifact_paths_for_sql_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)
            self._write_env(clientdir)

            with patch('core.base_migration.psycopg2.connect', return_value=FakeConnection()), \
                 patch('core.base_migration.firebirdsql.connect', return_value=FakeConnection()):
                with redirect_stdout(StringIO()):
                    migration = ArtifactMigration(clientdir)
                self.assertEqual(migration.file.name, migration.artifacts.sql_path('setup'))

                with redirect_stdout(StringIO()):
                    migration.run()

            self.assertTrue(os.path.exists(migration.artifacts.sql_path('setup')))
            self.assertTrue(os.path.exists(migration.artifacts.sql_path('prepare_target')))
            self.assertTrue(os.path.exists(migration.artifacts.sql_path('load_transformed_data')))
            self.assertTrue(os.path.exists(migration.artifacts.sql_path('finalize_target')))
            self.assertFalse(os.path.exists(os.path.join(clientdir, 'sql')))

            with open(migration.artifacts.sql_path('prepare_target'), encoding='utf-8') as sql_file:
                self.assertEqual(sql_file.read(), '-- prepare\n')
            with open(migration.artifacts.log_path('migration.log'), encoding='utf-8') as log_file:
                self.assertIn('Artefatos da execução:', log_file.read())
            with open(migration.artifacts.manifest_path, encoding='utf-8') as manifest_file:
                manifest = json.load(manifest_file)

            self.assertEqual(manifest['status'], 'success')
            self.assertFalse(manifest['debug']['enabled'])

    def test_dump_tables_writes_dump_to_contextual_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)
            self._write_env(clientdir)

            with patch('core.base_migration.psycopg2.connect', return_value=FakeConnection()), \
                 patch('core.base_migration.firebirdsql.connect', return_value=FakeConnection()), \
                 patch('core.base_migration.subprocess.call', return_value=0) as subprocess_call:
                with redirect_stdout(StringIO()):
                    migration = ArtifactMigration(clientdir)
                    migration.dump_tables()

            command_args = subprocess_call.call_args.args[0]
            self.assertIn(migration.artifacts.sql_path('dump_compatible_tables'), command_args)
            self.assertIn('public.acme_ignore_demo', command_args)

    def test_debug_mode_is_recorded_in_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)
            self._write_env(clientdir)

            with patch('core.base_migration.psycopg2.connect', return_value=FakeConnection()), \
                 patch('core.base_migration.firebirdsql.connect', return_value=FakeConnection()):
                with redirect_stdout(StringIO()):
                    migration = ArtifactMigration(clientdir)
                    migration.enable_debug(25)

            with open(migration.artifacts.manifest_path, encoding='utf-8') as manifest_file:
                manifest = json.load(manifest_file)

            self.assertEqual(manifest['status'], 'running')
            self.assertEqual(manifest['debug'], {'enabled': True, 'limit': 25})


if __name__ == '__main__':
    unittest.main()
