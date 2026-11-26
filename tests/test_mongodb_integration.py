"""Integration test of the MongoDB adapter against a real MongoDB.

Skipped unless ``MONGODB_TEST_URI`` points to a replica set, such as
``mongodb://hermes-mongo-rs:27017/?replicaSet=rs0``. With
``MONGODB_STANDALONE_URI`` it also checks that a server outside a replica set
is refused. The test creates and drops its own databases.
"""

import datetime
import decimal
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

URI = os.environ.get('MONGODB_TEST_URI')
STANDALONE_URI = os.environ.get('MONGODB_STANDALONE_URI')
SOURCE_DB = 'hermes_teste_origem'
TARGET_DB = 'hermes_teste_destino'


@unittest.skipUnless(URI, 'defina MONGODB_TEST_URI com um replica set para o teste de integração')
class MongoRoundTripTest(unittest.TestCase):
    def setUp(self):
        import pymongo
        from bson.decimal128 import Decimal128

        self.client = pymongo.MongoClient(URI, tz_aware=True, uuidRepresentation='standard')
        for name in (SOURCE_DB, TARGET_DB):
            self.client.drop_database(name)
        source = self.client[SOURCE_DB]
        source.alunos.insert_many([
            {'cod': 1, 'nome': 'Ana', 'situacao': 'A', 'nascimento': datetime.datetime(1990, 10, 2, tzinfo=datetime.timezone.utc),
             'nota': Decimal128('9.50'), 'foto': b'\x00\x01', 'endereco': {'cidade': 'Recife'}},
            {'cod': 2, 'nome': 'Bia', 'situacao': 'C', 'nascimento': None,
             'nota': Decimal128('7.25'), 'foto': None, 'endereco': {'cidade': 'Natal'}},
        ])
        source.feriados.insert_many([{'id': 1, 'dia': '01/01'}, {'id': 2, 'dia': '21/04'}])
        target = self.client[TARGET_DB]
        target.status.insert_many([{'codigo': 'A', 'id': 10}, {'codigo': 'C', 'id': 40}])
        # A collection must exist before a transaction writes to it on MongoDB older than 4.4.
        target.create_collection('pessoas')
        target.create_collection('feriados')
        target.pessoas.insert_one({'_id': 99, 'nome': 'antiga'})

        self._tmpdir = tempfile.TemporaryDirectory()
        self.clientdir = os.path.join(self._tmpdir.name, 'MongoTeste')
        os.makedirs(self.clientdir)
        with open(os.path.join(self.clientdir, '.env'), 'w', encoding='utf-8') as env_file:
            env_file.write('\n'.join([
                'SOURCE_M_ADAPTER=mongodb', f'SOURCE_M_URI={URI}', f'SOURCE_M_NAME={SOURCE_DB}',
                'TARGET_ADAPTER=mongodb', f'TARGET_URI={URI}', f'TARGET_NAME={TARGET_DB}',
            ]))

    def tearDown(self):
        for name in (SOURCE_DB, TARGET_DB):
            self.client.drop_database(name)
        self.client.close()
        self._tmpdir.cleanup()

    def _generate(self):
        from core.compatible import CopyEntity
        from core.context import DefaultMigrationContextFactory
        from core.queries import SourceQuery
        from core.references import ReferenceMap
        from core.runner import MigrationRunner
        from core.strategies import Copy, Lookup, Rename
        from core.table_migration import TableMigration

        context = DefaultMigrationContextFactory.from_clientdir(self.clientdir)

        class Migration:
            def declare_reference_maps(self):
                return [ReferenceMap('status', table='status', key='codigo', value='id')]

            def run_initial(self):
                from core.operations import Clear
                context.emit(Clear(['pessoas', 'feriados']))

            def compatible_entities(self):
                return [CopyEntity('feriados', source='m', order_by='id')]

            def run_inserts(self):
                TableMigration(SourceQuery('alunos', order_by='cod'), 'pessoas', [
                    Rename('cod', '_id'),
                    Copy('nome'),
                    Copy('nascimento'),
                    Copy('nota'),
                    Copy('foto'),
                    Rename('endereco.cidade', 'cidade'),
                    Lookup('situacao', context.reference_map('status'), v2_name='status_id'),
                ]).run(context.source('m'))

        with redirect_stdout(StringIO()):
            MigrationRunner(context, Migration()).run()
        context.close()
        return context.artifacts

    def _apply(self, artifacts, mode, uri=URI):
        from core.adapters.mongodb import MongoTargetAdapter
        from core.apply import ArtifactApplier
        from core.apply.cli import session_opener
        from core.connections import ConnectionConfig

        config = ConnectionConfig('TARGET', 'target', 'mongodb', {'uri': uri, 'name': TARGET_DB})
        return ArtifactApplier(artifacts.run_dir, session_opener(MongoTargetAdapter, config), mode=mode).apply()

    def test_generates_dry_runs_and_applies_with_types_preserved(self):
        from bson.decimal128 import Decimal128

        artifacts = self._generate()
        with open(artifacts.manifest_path, encoding='utf-8') as manifest_file:
            manifest = manifest_file.read()
        self.assertIn('"status": "success"', manifest)
        self.assertIn('comandos/03_load_transformed_data.jsonl', manifest)
        self.assertNotIn(URI, manifest)

        dry_run = self._apply(artifacts, 'dry-run')
        self.assertEqual(dry_run.result, 'rolled_back')
        # No savepoints in MongoDB: the dry run stops at the first error.
        self.assertEqual(dry_run.on_error, 'stop')
        self.assertEqual(list(self.client[TARGET_DB].pessoas.find()), [{'_id': 99, 'nome': 'antiga'}])

        applied = self._apply(artifacts, 'apply')
        self.assertEqual(applied.result, 'committed')
        people = list(self.client[TARGET_DB].pessoas.find().sort('_id', 1))
        self.assertEqual([person['_id'] for person in people], [1, 2])
        ana = people[0]
        self.assertEqual(ana['nascimento'], datetime.datetime(1990, 10, 2, tzinfo=datetime.timezone.utc))
        self.assertEqual(ana['nota'], Decimal128('9.50'))
        self.assertEqual(ana['foto'], b'\x00\x01')
        self.assertEqual(ana['cidade'], 'Recife')
        self.assertEqual([person['status_id'] for person in people], [10, 40])
        self.assertEqual(self.client[TARGET_DB].feriados.count_documents({}), 2)
        self.assertEqual(people[1]['nota'].to_decimal(), decimal.Decimal('7.25'))

    def test_duplicate_key_in_a_batch_aborts_everything_and_names_the_command(self):
        artifacts = self._generate()
        load = os.path.join(artifacts.commands_dir, '03_load_transformed_data.jsonl')
        with open(load, encoding='utf-8') as artifact:
            lines = artifact.readlines()
        with open(load, 'a', encoding='utf-8') as artifact:
            artifact.write(lines[0])

        report = self._apply(artifacts, 'apply')

        self.assertEqual(report.result, 'failed')
        self.assertIn(f'03_load_transformed_data.jsonl, comando {len(lines) + 1}', report.failure)
        with open(report.errors_path, encoding='utf-8') as errors_file:
            self.assertIn('código 11000', errors_file.read())
        self.assertEqual(list(self.client[TARGET_DB].pessoas.find()), [{'_id': 99, 'nome': 'antiga'}])

    @unittest.skipUnless(STANDALONE_URI, 'defina MONGODB_STANDALONE_URI para conferir a recusa fora de replica set')
    def test_standalone_server_is_refused(self):
        artifacts = self._generate()

        report = self._apply(artifacts, 'dry-run', uri=STANDALONE_URI)

        self.assertEqual(report.result, 'failed')
        self.assertIn('transações', report.failure)


if __name__ == '__main__':
    unittest.main()
