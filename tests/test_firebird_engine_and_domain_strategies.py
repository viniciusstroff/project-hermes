import sys
import types
import unittest

psycopg2 = types.ModuleType('psycopg2')
psycopg2_extras = types.ModuleType('psycopg2.extras')
psycopg2_extras.RealDictCursor = object
psycopg2.extras = psycopg2_extras
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)

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
            rows=[(1, '(11) 98888-7777', '123.456.789-00')],
            description=[('F_ID',), ('F_PHONE',), ('F_CPF',)],
        )
        writes = []

        engine = MigrationEngine(conn, writes.append, limit='LIMIT 10', source='firebird')

        TableMigration(
            source_sql='SELECT {fields} FROM users {limit}',
            target='public.users',
            fields=[Copy('f_id'), PhoneClean('f_phone'), CpfClean('f_cpf')],
        ).run(engine)

        self.assertEqual(
            conn.cursor_calls[0].executed_sql,
            'SELECT COUNT(*) FROM (SELECT f_id, f_phone, f_cpf FROM users ROWS 10) progress_src',
        )
        self.assertEqual(
            conn.cursor_calls[1].executed_sql,
            'SELECT f_id, f_phone, f_cpf FROM users ROWS 10',
        )
        self.assertEqual(
            writes,
            ["INSERT INTO public.users (f_id, f_phone, f_cpf) VALUES (1, E'11988887777', E'12345678900');\n"],
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
        self.assertEqual(strategy.value({'f_mobile2': '(51) 9 9988-7766'}), "E'51999887766'")

    def test_cpf_clean_removes_formatting(self):
        strategy = CpfClean('f_cpf')
        self.assertEqual(strategy.insert_col, 'f_cpf')
        self.assertEqual(strategy.value({'f_cpf': '123.456.789-00'}), "E'12345678900'")


if __name__ == '__main__':
    unittest.main()
