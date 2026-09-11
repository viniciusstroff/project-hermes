import json
import os
import sys
import tempfile
import types
import unittest

psycopg2 = types.ModuleType('psycopg2')
psycopg2_extras = types.ModuleType('psycopg2.extras')
psycopg2_extras.RealDictCursor = object
psycopg2.extras = psycopg2_extras
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)

firebirdsql = types.ModuleType('firebirdsql')
sys.modules.setdefault('firebirdsql', firebirdsql)

from core.artifacts import ArtifactManager


class ArtifactManagerTest(unittest.TestCase):
    def test_creates_run_directories_and_sql_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)

            artifacts = ArtifactManager(clientdir, timestamp='20260910-220000')

            self.assertEqual(artifacts.client_name, 'Acme')
            self.assertEqual(
                artifacts.run_dir,
                os.path.join(tmpdir, 'runs', 'Acme', '20260910-220000'),
            )
            self.assertTrue(os.path.isdir(artifacts.sql_dir))
            self.assertTrue(os.path.isdir(artifacts.logs_dir))
            self.assertTrue(os.path.isdir(artifacts.errors_dir))
            self.assertEqual(
                artifacts.sql_path('dump_compatible_tables'),
                os.path.join(artifacts.sql_dir, '02_dump_compatible_tables.sql'),
            )

    def test_manifest_omits_passwords(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)
            artifacts = ArtifactManager(clientdir, timestamp='20260910-220000')

            artifacts.write_manifest(
                status='running',
                debug_limit=100,
                config={
                    'PG_V1_HOST': 'source-db',
                    'PG_V1_PORT': '5432',
                    'PG_V1_NAME': 'source',
                    'PG_V1_USER': 'source-user',
                    'PG_V1_PASS': 'secret-source',
                    'PG_V2_HOST': 'target-db',
                    'PG_V2_PASS': 'secret-target',
                    'FB_V1_PASS': 'secret-firebird',
                },
            )

            with open(artifacts.manifest_path, encoding='utf-8') as manifest_file:
                manifest = json.load(manifest_file)

            self.assertEqual(manifest['client'], 'Acme')
            self.assertEqual(manifest['status'], 'running')
            self.assertEqual(manifest['debug'], {'enabled': True, 'limit': 100})
            self.assertEqual(manifest['databases']['pg_v1']['host'], 'source-db')
            self.assertNotIn('pass', json.dumps(manifest).lower())
            self.assertNotIn('secret-source', json.dumps(manifest))
            self.assertNotIn('secret-target', json.dumps(manifest))
            self.assertNotIn('secret-firebird', json.dumps(manifest))


if __name__ == '__main__':
    unittest.main()
