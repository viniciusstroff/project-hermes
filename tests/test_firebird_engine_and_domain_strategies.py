import sys
import types
import unittest

psycopg2 = types.ModuleType('psycopg2')
psycopg2_extras = types.ModuleType('psycopg2.extras')
psycopg2_extras.RealDictCursor = object
psycopg2.extras = psycopg2_extras
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)

from core.adapters import FirebirdSourceAdapter, PostgresTargetAdapter
from core.migration_engine import MigrationEngine
from core.queries import SourceQuery
from tests.source_fakes import sql_writer
from core.table_migration import TableMigration
from core.strategies import Copy, CpfClean, PhoneClean


class FakeFirebirdCursor:
    def __init__(self, rows, description):
        self.rows = rows
        self.description = description
        self.executed_sql = None
        self.closed = False

    def execute(self, sql):
        self.executed_sql = sql

    def fetchone(self):
        return [len(self.rows)]

    def __iter__(self):
        return iter(self.rows)

    def close(self):
        self.closed = True


class FakeFirebirdConnection:
    def __init__(self, rows, description):
        self.rows = rows
        self.description = description
        self.cursor_calls = []

    def cursor(self):
        cursor = FakeFirebirdCursor(self.rows, self.description)
        self.cursor_calls.append(cursor)
        return cursor


class FirebirdEngineTest(unittest.TestCase):
    def test_renders_rows_limit_and_maps_rows_to_dict(self):
        conn = FakeFirebirdConnection(
            rows=[(1, '(00) 90000-0000', '000.000.000-00')],
            description=[('F_ID',), ('F_PHONE',), ('F_CPF',)],
        )
        writes = []

        engine = MigrationEngine(
            FirebirdSourceAdapter(conn),
            PostgresTargetAdapter(),
            sql_writer(writes),
            limit=10,
        )

        TableMigration(
            source=SourceQuery('acme_v1_users_demo'),
            target='public.acme_users_demo',
            fields=[Copy('f_id'), PhoneClean('f_phone'), CpfClean('f_cpf')],
        ).run(engine)

        self.assertEqual(
            conn.cursor_calls[0].executed_sql,
            'SELECT COUNT(*) FROM (SELECT f_id, f_phone, f_cpf FROM acme_v1_users_demo ROWS 10) progress_src',
        )
        self.assertEqual(
            conn.cursor_calls[1].executed_sql,
            'SELECT f_id, f_phone, f_cpf FROM acme_v1_users_demo ROWS 10',
        )
        self.assertEqual(
            writes,
            ["INSERT INTO public.acme_users_demo (f_id, f_phone, f_cpf) VALUES (1, '00900000000', '00000000000');\n"],
        )
        self.assertTrue(all(cursor.closed for cursor in conn.cursor_calls))

    def test_reserved_words_are_quoted_in_upper_case(self):
        conn = FakeFirebirdConnection(rows=[], description=[('USER',)])

        list(FirebirdSourceAdapter(conn).read(SourceQuery('pedidos', fields=('user', 'f_id'), order_by='date')))

        self.assertEqual(conn.cursor_calls[0].executed_sql, 'SELECT "USER", f_id FROM pedidos ORDER BY "DATE"')


class DomainStrategiesTest(unittest.TestCase):
    def test_phone_clean_supports_rename(self):
        strategy = PhoneClean('f_mobile2', 'f_number1')
        self.assertEqual(strategy.insert_col, 'f_number1')
        self.assertEqual(strategy.raw_value({'f_mobile2': '(00) 9 0000-0000'}), '00900000000')

    def test_cpf_clean_removes_formatting(self):
        strategy = CpfClean('f_cpf')
        self.assertEqual(strategy.insert_col, 'f_cpf')
        self.assertEqual(strategy.raw_value({'f_cpf': '000.000.000-00'}), '00000000000')


if __name__ == '__main__':
    unittest.main()
