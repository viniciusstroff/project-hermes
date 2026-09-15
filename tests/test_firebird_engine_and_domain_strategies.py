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
from core.table_migration import TableMigration
from core.strategies import Copy, CpfClean, PhoneClean, SqlExpression


class FakeFirebirdCursor:
    def __init__(self, rows, description):
        self.rows = rows
        self.description = description
        self.executed_sql = None

    def execute(self, sql):
        self.executed_sql = sql

    def fetchone(self):
        return [len(self.rows)]

    def __iter__(self):
        return iter(self.rows)


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
    def test_translates_limit_and_maps_rows_to_dict(self):
        conn = FakeFirebirdConnection(
            rows=[(1, '(00) 90000-0000', '000.000.000-00')],
            description=[('F_ID',), ('F_PHONE',), ('F_CPF',)],
        )
        writes = []

        engine = MigrationEngine(
            FirebirdSourceAdapter(conn),
            PostgresTargetAdapter(),
            writes.append,
            limit='LIMIT 10',
        )

        TableMigration(
            source_sql='SELECT {fields} FROM acme_v1_users_demo {limit}',
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
            ["INSERT INTO public.acme_users_demo (f_id, f_phone, f_cpf) VALUES (1, E'00900000000', E'00000000000');\n"],
        )


class DomainStrategiesTest(unittest.TestCase):
    def test_sql_expression_projects_alias(self):
        strategy = SqlExpression("COALESCE(f_type, 1)", 'f_type')
        self.assertEqual(strategy.select_columns, ["COALESCE(f_type, 1) AS f_type"])
        self.assertEqual(strategy.insert_col, 'f_type')
        self.assertEqual(strategy.value({'f_type': 1}), '1')

    def test_phone_clean_supports_rename(self):
        strategy = PhoneClean('f_mobile2', 'f_number1')
        self.assertEqual(strategy.insert_col, 'f_number1')
        self.assertEqual(strategy.value({'f_mobile2': '(00) 9 0000-0000'}), "E'00900000000'")

    def test_cpf_clean_removes_formatting(self):
        strategy = CpfClean('f_cpf')
        self.assertEqual(strategy.insert_col, 'f_cpf')
        self.assertEqual(strategy.value({'f_cpf': '000.000.000-00'}), "E'00000000000'")


if __name__ == '__main__':
    unittest.main()
