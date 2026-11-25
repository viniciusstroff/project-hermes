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

    def test_manifest_records_sources_and_target(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)
            artifacts = ArtifactManager(clientdir, timestamp='20260910-220000')

            artifacts.write_manifest(
                status='running',
                debug_limit=100,
                connections={
                    'sources': {'v1': {'adapter': 'postgres', 'host': 'source-db', 'name': 'source'}},
                    'target': {'adapter': 'postgres', 'host': 'target-db'},
                },
            )

            with open(artifacts.manifest_path, encoding='utf-8') as manifest_file:
                manifest = json.load(manifest_file)

            self.assertEqual(manifest['client'], 'Acme')
            self.assertEqual(manifest['status'], 'running')
            self.assertEqual(manifest['debug'], {'enabled': True, 'limit': 100})
            self.assertEqual(manifest['sources']['v1']['host'], 'source-db')
            self.assertEqual(manifest['target']['adapter'], 'postgres')
            self.assertNotIn('databases', manifest)


if __name__ == '__main__':
    unittest.main()
