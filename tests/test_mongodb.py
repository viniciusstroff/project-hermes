import datetime
import decimal
import importlib.util
import json
import os
import tempfile
import unittest

from core.adapters.mongodb import MongoSession, MongoSourceAdapter, MongoTargetAdapter
from core.application import Command, DeferredCommandError
from core.apply import APPLY, CONTINUE, DRY_RUN, STOP, ArtifactApplier
from core.application import CapabilityError
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
from core.queries import NativeAdapterMismatchError, NativeField, NativeQuery, SourceQuery, UnsupportedQueryError
from core.sql import SqlLiteral
from tests.apply_fakes import make_run

HAS_BSON = importlib.util.find_spec('bson') is not None
needs_bson = unittest.skipUnless(HAS_BSON, 'pymongo (bson) não instalado; roda na imagem do Hermes')


# ---------------------------------------------------------------------------
# Fake pymongo objects
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, documents, calls):
        self.documents = list(documents)
        self.calls = calls

    def sort(self, keys):
        self.calls.append(('sort', keys))
        return self

    def limit(self, limit):
        self.calls.append(('limit', limit))
        self.documents = self.documents[:limit]
        return self

    def __iter__(self):
        return iter(self.documents)


class BulkError(Exception):
    """Same shape as pymongo.errors.BulkWriteError."""

    def __init__(self, index, code=11000):
        self.details = {'writeErrors': [{'index': index, 'code': code, 'errmsg': 'E11000 duplicate key'}]}
        super().__init__('E11000 duplicate key error')


class FakeCollection:
    def __init__(self, name, database):
        self.name = name
        self.database = database

    @property
    def calls(self):
        return self.database.calls

    def find(self, filter, projection):
        self.calls.append(('find', self.name, filter, projection))
        return FakeCursor(self.database.documents.get(self.name, []), self.calls)

    def aggregate(self, pipeline):
        self.calls.append(('aggregate', self.name, pipeline))
        if pipeline and '$count' in pipeline[-1]:
            return iter([{'total': len(self.database.documents.get(self.name, []))}])
        return iter(self.database.documents.get(self.name, []))

    def count_documents(self, filter, **options):
        self.calls.append(('count_documents', self.name, filter, options))
        return len(self.database.documents.get(self.name, []))

    def insert_many(self, documents, ordered, session):
        self.calls.append(('insert_many', self.name, [dict(document) for document in documents]))
        if self.database.fail_insert is not None:
            raise BulkError(self.database.fail_insert)

    def delete_many(self, filter, session):
        self.calls.append(('delete_many', self.name, filter))


class FakeDatabase:
    def __init__(self, documents=None, fail_insert=None):
        self.name = 'acmeedu_v2'
        self.documents = documents or {}
        self.fail_insert = fail_insert
        self.calls = []

    def __getitem__(self, name):
        return FakeCollection(name, self)


class FakeClientSession:
    def __init__(self, calls):
        self.calls = calls
        self.in_transaction = False

    def start_transaction(self):
        self.calls.append(('start_transaction',))
        self.in_transaction = True

    def commit_transaction(self):
        self.calls.append(('commit_transaction',))
        self.in_transaction = False

    def abort_transaction(self):
        self.calls.append(('abort_transaction',))
        self.in_transaction = False

    def end_session(self):
        self.calls.append(('end_session',))


class FakeAdmin:
    def __init__(self, replica_set, limit):
        self.replica_set = replica_set
        self.limit = limit

    def command(self, command):
        if command == 'hello':
            return {'setName': 'rs0'} if self.replica_set else {'isWritablePrimary': True}
        if self.limit is None:
            raise PermissionError('not authorized on admin')
        return {'transactionLifetimeLimitSeconds': self.limit}


class FakeClient:
    def __init__(self, database, replica_set=True, limit=60):
        self.database = database
        self.admin = FakeAdmin(replica_set, limit)
        self.closed = False

    def start_session(self):
        return FakeClientSession(self.database.calls)

    def close(self):
        self.closed = True


class FakeDecimal128:
    def __init__(self, text):
        self.text = text

    def to_decimal(self):
        return decimal.Decimal(self.text)


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class MongoSourceTest(unittest.TestCase):
    def setUp(self):
        self.database = FakeDatabase({'tb_aluno': [
            {'cod_aluno': 1, 'cpf': '1', 'nota': FakeDecimal128('9.50'), 'endereco': {'cidade': 'Recife'}},
            {'cod_aluno': 2, 'cpf': '2', 'nota': FakeDecimal128('7.00'), 'endereco': {'cidade': 'Natal'}},
        ]})
        self.source = MongoSourceAdapter(self.database)

    def test_source_query_becomes_find_with_projection_sort_and_limit(self):
        rows = list(self.source.read(SourceQuery('tb_aluno', fields=('cod_aluno', 'cpf'), where={'ativo': True},
                                                 order_by='cod_aluno', limit=1)))

        self.assertEqual(self.database.calls[:3], [
            ('find', 'tb_aluno', {'ativo': True}, {'cod_aluno': 1, 'cpf': 1, '_id': 0}),
            ('sort', [('cod_aluno', 1)]),
            ('limit', 1),
        ])
        self.assertEqual(len(rows), 1)

    def test_id_only_when_asked_and_decimal128_becomes_decimal(self):
        list(self.source.read(SourceQuery('tb_aluno')))
        list(self.source.read(SourceQuery('tb_aluno', fields=('_id', 'cpf'))))

        self.assertEqual(self.database.calls[0][3], {'_id': 0})
        self.assertEqual(self.database.calls[1][3], {'_id': 1, 'cpf': 1})
        row = next(self.source.read(SourceQuery('tb_aluno')))
        self.assertEqual(row['nota'], decimal.Decimal('9.50'))

    def test_dotted_field_comes_under_its_path(self):
        row = next(self.source.read(SourceQuery('tb_aluno', fields=('cod_aluno', 'endereco.cidade'))))

        self.assertEqual(row['endereco.cidade'], 'Recife')

    def test_native_query_is_an_aggregation_with_the_limit_at_the_end(self):
        pipeline = [{'$match': {'ativo': True}}]
        list(self.source.read(NativeQuery('mongodb', {'collection': 'tb_aluno', 'pipeline': pipeline}, limit=5)))

        self.assertEqual(self.database.calls[0], ('aggregate', 'tb_aluno', [{'$match': {'ativo': True}}, {'$limit': 5}]))

    def test_native_field_turns_the_query_into_a_pipeline(self):
        field = NativeField('mongodb', {'$concat': ['$cpf', '-x']}, 'cpf_x')
        list(self.source.read(SourceQuery('tb_aluno', fields=('cod_aluno', field), order_by='cod_aluno', limit=3)))

        self.assertEqual(self.database.calls[0], ('aggregate', 'tb_aluno', [
            {'$sort': {'cod_aluno': 1}},
            {'$limit': 3},
            {'$project': {'_id': 0, 'cod_aluno': 1, 'cpf_x': {'$concat': ['$cpf', '-x']}}},
        ]))

    def test_count(self):
        self.assertEqual(self.source.count(SourceQuery('tb_aluno', where={'ativo': True}, limit=10)), 2)
        self.assertEqual(self.database.calls[-1], ('count_documents', 'tb_aluno', {'ativo': True}, {'limit': 10}))
        self.assertEqual(self.source.count(NativeQuery('mongodb', {'collection': 'tb_aluno', 'pipeline': []})), 2)
        self.assertEqual(self.database.calls[-1][2], [{'$count': 'total'}])

    def test_refusals_read_nothing(self):
        cases = [
            (NativeQuery('postgres', 'SELECT 1'), NativeAdapterMismatchError),
            (SourceQuery('tb_aluno', fields=(NativeField('postgres', 'upper(cpf)', 'cpf'),)), NativeAdapterMismatchError),
            (NativeQuery('mongodb', [{'$match': {}}]), UnsupportedQueryError),
        ]
        for query, error in cases:
            with self.subTest(query=query), self.assertRaises(error):
                self.source.read(query)
        self.assertEqual(self.database.calls, [])


# ---------------------------------------------------------------------------
# Target rendering
# ---------------------------------------------------------------------------


@needs_bson
class MongoRenderTest(unittest.TestCase):
    def setUp(self):
        self.target = MongoTargetAdapter()

    def test_insert_keeps_date_decimal_and_binary_in_extended_json(self):
        (line,) = self.target.render(Insert('people', {
            'id': 1,
            'birth_date': datetime.date(1990, 10, 2),
            'grade': decimal.Decimal('85.00'),
            'photo': b'\x00\x01',
            'name': "O'Brien",
            'notes': None,
        }))

        document = json.loads(line)
        self.assertEqual(document['op'], 'insert')
        self.assertEqual(document['collection'], 'people')
        self.assertEqual(document['document']['birth_date'], {'$date': '1990-10-02T00:00:00Z'})
        self.assertEqual(document['document']['grade'], {'$numberDecimal': '85.00'})
        self.assertEqual(document['document']['photo']['$binary']['base64'], 'AAE=')
        self.assertEqual(document['document']['name'], "O'Brien")
        self.assertTrue(line.endswith('\n'))

    def test_operations_without_meaning_write_nothing(self):
        for operation in (SuspendConstraints(), ResumeConstraints(['people']), ResetIdentity('people'), Comment('X')):
            with self.subTest(operation=operation):
                self.assertEqual(self.target.render(operation), [])

    def test_clear_is_delete_many_per_collection(self):
        lines = [json.loads(line) for line in self.target.render(Clear(['a', 'b']))]

        self.assertEqual(lines, [
            {'op': 'delete_many', 'collection': 'a', 'filter': {}},
            {'op': 'delete_many', 'collection': 'b', 'filter': {}},
        ])

    def test_native_of_another_adapter_and_sql_literal_are_refused(self):
        with self.assertRaises(NativeAdapterMismatchError):
            self.target.render(Native('postgres', 'TRUNCATE a;'))
        with self.assertRaises(ValueSerializationError) as raised:
            self.target.render(Insert('people', {'created_at': SqlLiteral('now()')}))
        self.assertEqual((raised.exception.table, raised.exception.column), ('people', 'created_at'))
        for value in (datetime.time(10, 30), 2 ** 70, object()):
            with self.subTest(value=value), self.assertRaises(ValueSerializationError):
                self.target.render(Insert('people', {'x': value}))

    def test_native_mongodb_operation_is_written_as_is(self):
        payload = {'op': 'update_many', 'collection': 'people', 'filter': {}, 'update': {'$set': {'a': 1}}}

        (line,) = self.target.render(Native('mongodb', payload))

        self.assertEqual(json.loads(line), payload)

    def test_lines_read_back_with_the_same_types(self):
        values = {'birth': datetime.date(1990, 10, 2), 'grade': decimal.Decimal('85.00'), 'photo': b'\x00\x01'}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, '03.jsonl')
            with open(path, 'w', encoding='utf-8') as artifact:
                artifact.writelines(self.target.render(Insert('people', values)))
                artifact.write('\nisto não é json\n')
            session = MongoSession(FakeClient(FakeDatabase()), FakeDatabase())
            commands = list(session.commands(path, dry_run=False))

        document = commands[0].payload['document']
        self.assertEqual(document['birth'], datetime.datetime(1990, 10, 2, tzinfo=datetime.timezone.utc))
        self.assertEqual(document['grade'].to_decimal(), decimal.Decimal('85.00'))
        self.assertEqual(document['photo'], b'\x00\x01')
        self.assertEqual(commands[1].line, 3)
        self.assertIn('linha inválida', commands[1].error)


# ---------------------------------------------------------------------------
# Application session
# ---------------------------------------------------------------------------


def insert(collection, document_id):
    return Command('', 1, payload={'op': 'insert', 'collection': collection, 'document': {'_id': document_id}})


class MongoSessionTest(unittest.TestCase):
    def test_capabilities_follow_the_deployment(self):
        replica = MongoSession(FakeClient(FakeDatabase(), replica_set=True), FakeDatabase())
        standalone = MongoSession(FakeClient(FakeDatabase(), replica_set=False, limit=None), FakeDatabase())

        self.assertTrue(replica.capabilities.transactions)
        self.assertFalse(replica.capabilities.savepoints)
        self.assertFalse(standalone.capabilities.transactions)
        self.assertEqual(replica.describe()['transaction_limit_seconds'], 60)
        self.assertNotIn('transaction_limit_seconds', standalone.describe())

    def test_consecutive_inserts_go_in_one_batch_until_another_operation(self):
        database = FakeDatabase()
        session = MongoSession(FakeClient(database), database)
        session.begin()

        for document_id in (1, 2, 3):
            session.execute(insert('people', document_id))
        self.assertEqual(session.pending, 3)
        session.execute(insert('phones', 10))
        session.execute(Command('', 5, payload={'op': 'delete_many', 'collection': 'tmp', 'filter': {}}))
        session.commit()

        self.assertEqual(database.calls, [
            ('start_transaction',),
            ('insert_many', 'people', [{'_id': 1}, {'_id': 2}, {'_id': 3}]),
            ('insert_many', 'phones', [{'_id': 10}]),
            ('delete_many', 'tmp', {}),
            ('commit_transaction',),
        ])
        self.assertEqual(session.pending, 0)

    def test_batch_failure_names_the_position_and_the_code(self):
        database = FakeDatabase(fail_insert=1)
        session = MongoSession(FakeClient(database), database)
        session.begin()
        session.execute(insert('people', 1))
        session.execute(insert('people', 1))

        with self.assertRaises(DeferredCommandError) as raised:
            session.flush()

        self.assertEqual(raised.exception.position, 1)
        self.assertEqual(session.error_code(raised.exception.cause), 11000)


class MongoApplierTest(unittest.TestCase):
    FILES = {
        '01_prepare_target.jsonl': '{"op": "delete_many", "collection": "people", "filter": {}}\n',
        '03_load_transformed_data.jsonl': ''.join(
            json.dumps({'op': 'insert', 'collection': 'people', 'document': {'_id': number}}) + '\n'
            for number in (1, 2, 3)
        ),
    }

    def _run(self, tmpdir, database, **options):
        run_dir = make_run(tmpdir, dict(self.FILES), target='mongodb')
        client = FakeClient(database)
        return ArtifactApplier(run_dir, lambda: MongoSession(client, database), **options)

    @needs_bson
    def test_applies_in_one_transaction_with_batched_inserts(self):
        database = FakeDatabase()
        with tempfile.TemporaryDirectory() as tmpdir:
            report = self._run(tmpdir, database).apply()

        self.assertEqual(report.result, 'committed')
        self.assertIn(('insert_many', 'people', [{'_id': 1}, {'_id': 2}, {'_id': 3}]), database.calls)
        self.assertEqual(database.calls[-2:], [('commit_transaction',), ('end_session',)])
        self.assertEqual(report.as_dict()['files'][1]['executed'], 3)
        self.assertEqual(report.as_dict()['target']['adapter'], 'mongodb')

    @needs_bson
    def test_failed_insert_of_a_batch_names_its_command(self):
        database = FakeDatabase(fail_insert=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            report = self._run(tmpdir, database).apply()
            with open(report.errors_path, encoding='utf-8') as errors_file:
                errors = errors_file.read()

        self.assertEqual(report.result, 'failed')
        self.assertTrue(report.failure.startswith('03_load_transformed_data.jsonl, comando 2, linha 2:'))
        self.assertIn('código 11000', errors)
        self.assertIn(('abort_transaction',), database.calls)

    def test_policies_follow_the_capabilities(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            applier = self._run(tmpdir, FakeDatabase(), mode=DRY_RUN)
            self.assertEqual(applier.on_error, STOP)
            with self.assertRaises(CapabilityError):
                self._run(tmpdir + '/b', FakeDatabase(), mode=APPLY, on_error=CONTINUE)

    def test_standalone_server_is_refused_before_any_command(self):
        database = FakeDatabase()
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = make_run(tmpdir, dict(self.FILES), target='mongodb')
            client = FakeClient(database, replica_set=False)
            report = ArtifactApplier(run_dir, lambda: MongoSession(client, database), mode=DRY_RUN).apply()

        self.assertEqual(report.result, 'failed')
        self.assertIn('transações', report.failure)
        self.assertEqual([call for call in database.calls if call[0] in ('insert_many', 'delete_many')], [])


if __name__ == '__main__':
    unittest.main()
