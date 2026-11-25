import unittest

from core.adapters.postgres import PostgresTargetAdapter
from core.adapters.registry import AdapterResolutionError, resolve_adapter
from core.migration_engine import MigrationEngine
from core.queries import (
    NativeAdapterMismatchError,
    NativeField,
    NativeQuery,
    SourceQuery,
    UnsupportedQueryError,
    check_source,
    with_default_fields,
    with_limit,
)
from core.strategies import Copy
from core.table_migration import TableMigration
from tests.source_fakes import FakeSqlSource

TEACHERS = 'SELECT p.cod_prof FROM public.tb_professor p LEFT JOIN public.tb_aluno a ON a.cpf = p.cpf ORDER BY 1;'


class SourceQueryTest(unittest.TestCase):
    def test_normalizes_fields_ordering_and_filters(self):
        where = {'f_active': True}
        query = SourceQuery('public.t', fields=['a', 'b'], where=where, order_by='a')

        where['f_other'] = 1

        self.assertEqual(query.fields, ('a', 'b'))
        self.assertEqual(query.order_by, ('a',))
        self.assertEqual(query.where, {'f_active': True})

    def test_refuses_invalid_entity_and_limit(self):
        for entity in ('', '  ', None):
            with self.subTest(entity=entity), self.assertRaises(TypeError):
                SourceQuery(entity)
        for limit in (-1, '10', True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(TypeError):
                SourceQuery('t', limit=limit)
            with self.subTest(native_limit=limit), self.assertRaises(TypeError):
                NativeQuery('postgres', 'SELECT 1', limit=limit)

    def test_native_query_needs_the_adapter(self):
        with self.assertRaises(TypeError):
            NativeQuery('', 'SELECT 1')

    def test_with_limit_keeps_the_smaller(self):
        query = SourceQuery('t')

        self.assertIs(with_limit(query, None), query)
        self.assertEqual(with_limit(query, 10).limit, 10)
        self.assertEqual(with_limit(SourceQuery('t', limit=5), 10).limit, 5)
        self.assertEqual(with_limit(SourceQuery('t', limit=50), 10).limit, 10)
        self.assertEqual(with_limit(NativeQuery('postgres', 'SELECT 1'), 3).limit, 3)

    def test_default_fields_only_fill_an_empty_source_query(self):
        native = NativeQuery('postgres', 'SELECT 1')
        explicit = SourceQuery('t', fields=['x'])

        self.assertEqual(with_default_fields(SourceQuery('t'), ['a', 'b']).fields, ('a', 'b'))
        self.assertIs(with_default_fields(explicit, ['a']), explicit)
        self.assertIs(with_default_fields(native, ['a']), native)

    def test_migrations_refuse_a_sql_string(self):
        with self.assertRaises(TypeError) as raised:
            TableMigration('SELECT {fields} FROM t {limit}', 'public.t', [Copy('a')])

        self.assertIn('NativeQuery', str(raised.exception))
        self.assertIs(check_source(SourceQuery('t')).__class__, SourceQuery)


class PostgresRenderingTest(unittest.TestCase):
    def setUp(self):
        self.source = FakeSqlSource('postgres')

    def test_source_query(self):
        query = SourceQuery(
            'public.Pedidos',
            fields=('f_id', 'user', 'Tipo', NativeField('postgres', "NULLIF(f_cpf, '')", 'f_cpf')),
            where={'f_status': "O'B", 'f_active': False, 'f_deleted': None, 'order': 2},
            order_by=('f_id', 'user'),
            limit=10,
        )

        self.assertEqual(
            self.source.render(query),
            'SELECT f_id, "user", "Tipo", NULLIF(f_cpf, \'\') AS f_cpf FROM public."Pedidos" '
            "WHERE f_status = 'O''B' AND f_active = false AND f_deleted IS NULL AND \"order\" = 2 "
            'ORDER BY f_id, "user" LIMIT 10',
        )

    def test_source_query_without_fields_reads_everything(self):
        self.assertEqual(self.source.render(SourceQuery('t')), 'SELECT * FROM t')

    def test_native_query_runs_as_written_without_limit(self):
        self.assertEqual(self.source.render(NativeQuery('postgres', TEACHERS)), TEACHERS.rstrip(';'))

    def test_native_query_with_limit_is_wrapped(self):
        self.assertEqual(
            self.source.render(NativeQuery('postgres', TEACHERS, limit=5)),
            f"SELECT * FROM ({TEACHERS.rstrip(';')}) native_src LIMIT 5",
        )

    def test_count_wraps_the_query(self):
        self.source.rows = [{}, {}]

        self.assertEqual(self.source.count(SourceQuery('t', limit=3)), 2)
        self.assertEqual(self.source.queries, ['SELECT COUNT(*) FROM (SELECT * FROM t LIMIT 3) progress_src'])

    def test_native_query_must_be_text(self):
        with self.assertRaises(UnsupportedQueryError):
            self.source.render(NativeQuery('postgres', [{'$match': {}}]))


class FirebirdRenderingTest(unittest.TestCase):
    def test_limit_uses_rows(self):
        source = FakeSqlSource('firebird')

        self.assertEqual(source.render(SourceQuery('clientes', order_by='f_id', limit=5)),
                         'SELECT * FROM clientes ORDER BY f_id ROWS 5')
        self.assertEqual(source.render(NativeQuery('firebird', 'SELECT f_id FROM clientes', limit=5)),
                         'SELECT * FROM (SELECT f_id FROM clientes) native_src ROWS 5')


class RefusalTest(unittest.TestCase):
    def test_native_query_of_another_adapter_reads_nothing(self):
        source = FakeSqlSource('firebird', rows=[{'cod_prof': 1}])
        query = NativeQuery('postgres', TEACHERS)

        for call in (source.read, source.count):
            with self.subTest(call=call.__name__):
                with self.assertRaises(NativeAdapterMismatchError) as raised:
                    call(query)
                self.assertIn("'postgres'", str(raised.exception))
                self.assertIn("'firebird'", str(raised.exception))
        self.assertEqual(source.queries, [])

    def test_nested_field_is_refused_by_a_relational_source(self):
        source = FakeSqlSource('postgres')
        queries = [
            SourceQuery('pessoas', fields=['endereco.cidade']),
            SourceQuery('pessoas', where={'endereco.cidade': 'Recife'}),
            SourceQuery('pessoas', order_by='endereco.cidade'),
        ]
        for query in queries:
            with self.subTest(query=query), self.assertRaises(UnsupportedQueryError) as raised:
                source.read(query)
            self.assertIn('endereco.cidade', str(raised.exception))
        self.assertEqual(source.queries, [])

    def test_migration_fails_before_the_count(self):
        source = FakeSqlSource('firebird', rows=[{'cod_prof': 1}])
        migration = TableMigration(NativeQuery('postgres', TEACHERS), 'public.teachers', [Copy('cod_prof')])

        with self.assertRaises(NativeAdapterMismatchError):
            migration.run(MigrationEngine(source, PostgresTargetAdapter(), lambda sql: None))

        self.assertEqual(source.queries, [])


class EngineLimitTest(unittest.TestCase):
    def test_debug_limit_reaches_every_query_as_an_integer(self):
        source = FakeSqlSource('postgres', rows=[{'a': 1}])
        engine = MigrationEngine(source, PostgresTargetAdapter(), lambda sql: None, limit=3)

        list(engine.read(SourceQuery('t', fields=['a'])))
        list(engine.read(SourceQuery('t', fields=['a'], limit=2)))
        list(engine.read(NativeQuery('postgres', 'SELECT a FROM t')))

        self.assertEqual(source.queries, [
            'SELECT a FROM t LIMIT 3',
            'SELECT a FROM t LIMIT 2',
            'SELECT * FROM (SELECT a FROM t) native_src LIMIT 3',
        ])

    def test_total_rows_hides_count_failures_but_not_refusals(self):
        class BrokenCount(FakeSqlSource):
            def _scalar(self, sql):
                raise RuntimeError('count failed')

        engine = MigrationEngine(BrokenCount('postgres'), PostgresTargetAdapter(), lambda sql: None)

        self.assertIsNone(engine.total_rows(SourceQuery('t')))
        with self.assertRaises(UnsupportedQueryError):
            engine.total_rows(SourceQuery('t', fields=['a.b']))


class CursorOnlySource:
    """Source written for the old contract: cursor() instead of read() and count()."""

    @classmethod
    def connect(cls, config):
        return cls()

    def cursor(self):
        pass

    def close(self):
        pass


class SourceContractTest(unittest.TestCase):
    def test_registry_requires_read_and_count(self):
        with self.assertRaises(AdapterResolutionError) as raised:
            resolve_adapter(f'{__name__}:CursorOnlySource', 'source')

        self.assertIn('read, count', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
