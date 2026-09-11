import unittest
import sys
import types

psycopg2 = types.ModuleType('psycopg2')
psycopg2_extras = types.ModuleType('psycopg2.extras')
psycopg2_extras.RealDictCursor = object
psycopg2.extras = psycopg2_extras
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)

from core.migration_engine import MigrationEngine
from core.expand_migration import ExpandMigration
from core.table_migration import TableMigration
from core.multi_target_migration import MultiTargetMigration, TargetMigration
from core.strategies import Copy, Transform


class FakeField:
    def __init__(self, name):
        self.name = name

    @property
    def select_columns(self):
        return [self.name]

    @property
    def insert_col(self):
        return self.name

    def value(self, row):
        return str(row[self.name])


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed_sql = None

    def execute(self, sql):
        self.executed_sql = sql

    def fetchone(self):
        return [len(self.rows)]

    def __iter__(self):
        return iter(self.rows)


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.cursor_calls = []

    def cursor(self, *args, **kwargs):
        cursor = FakeCursor(self.rows)
        self.cursor_calls.append(cursor)
        return cursor


class TableMigrationProgressTest(unittest.TestCase):
    def test_rejects_invalid_field_strategy(self):
        with self.assertRaises(TypeError):
            TableMigration(
                source_sql='SELECT {fields} FROM public.source {limit}',
                target='public.target',
                fields=[object()],
            )

    def test_reports_progress_on_interval_and_completion(self):
        rows = [{'f_id': i} for i in range(1, 6)]
        conn = FakeConnection(rows)
        writes = []
        progress = []

        engine = MigrationEngine(
            conn,
            writes.append,
            progress_fn=lambda table, count, total, done: progress.append((table, count, total, done)),
            progress_every=2,
        )

        TableMigration(
            source_sql='SELECT {fields} FROM public.source {limit}',
            target='public.target',
            fields=[FakeField('f_id')],
        ).run(engine)

        self.assertEqual(
            progress,
            [
                ('public.target', 0, 5, False),
                ('public.target', 2, 5, False),
                ('public.target', 4, 5, False),
                ('public.target', 5, 5, True),
            ],
        )
        self.assertEqual(len(writes), 5)
        self.assertEqual(
            conn.cursor_calls[0].executed_sql,
            'SELECT COUNT(*) FROM (SELECT f_id FROM public.source ) progress_src',
        )
        self.assertEqual(
            conn.cursor_calls[1].executed_sql,
            'SELECT f_id FROM public.source ',
        )


class ExpandMigrationTest(unittest.TestCase):
    def test_expands_one_source_row_into_multiple_target_rows(self):
        conn = FakeConnection([
            {
                'f_id': 10,
                'f_appointmenttype': 2,
                'f_days': 3,
                'f_managervalidate': True,
                'f_maximum': 4,
                'f_status': 1,
            }
        ])
        writes = []
        progress = []

        ExpandMigration(
            source_sql='SELECT {fields} FROM public.rescheduling_appointmenttypes {limit}',
            target='public.rescheduling_appointmenttypes',
            fields=[
                Copy('f_appointmenttype'),
                Copy('f_days'),
                Copy('f_managervalidate'),
                Copy('f_maximum'),
                Copy('f_status'),
            ],
            first_fields=[
                Copy('f_id'),
                Copy('f_appointmenttype'),
                Copy('f_days'),
                Copy('f_managervalidate'),
                Copy('f_maximum'),
                Copy('f_status'),
            ],
            expand_col='f_state',
            expand_values=[1, 2, 3],
        ).run(
            MigrationEngine(
                conn,
                writes.append,
                progress_fn=lambda table, count, total, done: progress.append((table, count, total, done)),
                progress_every=2,
            )
        )

        self.assertEqual(
            conn.cursor_calls[0].executed_sql,
            'SELECT COUNT(*) FROM (SELECT f_id, f_appointmenttype, f_days, f_managervalidate, f_maximum, f_status FROM public.rescheduling_appointmenttypes ) progress_src',
        )
        self.assertEqual(
            conn.cursor_calls[1].executed_sql,
            'SELECT f_id, f_appointmenttype, f_days, f_managervalidate, f_maximum, f_status FROM public.rescheduling_appointmenttypes ',
        )
        self.assertEqual(
            writes,
            [
                'INSERT INTO public.rescheduling_appointmenttypes (f_id, f_appointmenttype, f_days, f_managervalidate, f_maximum, f_status, f_state) VALUES (10, 2, 3, true, 4, 1, 1);\n',
                'INSERT INTO public.rescheduling_appointmenttypes (f_appointmenttype, f_days, f_managervalidate, f_maximum, f_status, f_state) VALUES (2, 3, true, 4, 1, 2);\n',
                'INSERT INTO public.rescheduling_appointmenttypes (f_appointmenttype, f_days, f_managervalidate, f_maximum, f_status, f_state) VALUES (2, 3, true, 4, 1, 3);\n',
            ],
        )
        self.assertEqual(
            progress,
            [
                ('public.rescheduling_appointmenttypes', 0, 3, False),
                ('public.rescheduling_appointmenttypes', 2, 3, False),
                ('public.rescheduling_appointmenttypes', 3, 3, True),
            ],
        )

    def test_does_not_emit_progress_for_empty_result(self):
        conn = FakeConnection([])
        progress = []

        engine = MigrationEngine(
            conn,
            lambda sql: None,
            progress_fn=lambda table, count, total, done: progress.append((table, count, total, done)),
            progress_every=10,
        )

        TableMigration(
            source_sql='SELECT {fields} FROM public.source {limit}',
            target='public.target',
            fields=[FakeField('f_id')],
        ).run(engine)

        self.assertEqual(progress, [('public.target', 0, 0, False), ('public.target', 0, 0, True)])

    def test_deduplicates_select_columns(self):
        conn = FakeConnection([{'f_id': 1}])

        TableMigration(
            source_sql='SELECT {fields} FROM public.source {limit}',
            target='public.target',
            fields=[FakeField('f_id'), FakeField('f_id')],
        ).run(MigrationEngine(conn, lambda sql: None))

        self.assertEqual(
            conn.cursor_calls[0].executed_sql,
            'SELECT COUNT(*) FROM (SELECT f_id FROM public.source ) progress_src',
        )
        self.assertEqual(
            conn.cursor_calls[1].executed_sql,
            'SELECT f_id FROM public.source ',
        )


class MultiTargetMigrationTest(unittest.TestCase):
    def test_writes_multiple_targets_from_one_source_row(self):
        conn = FakeConnection([
            {
                'f_id': 9,
                'f_description': "texto\x01",
                'f_process_id': 0,
                'f_status': 2,
            }
        ])
        writes = []
        progress = []

        MultiTargetMigration(
            source_sql='SELECT {fields} FROM public.publications {limit}',
            label='public.publications',
            targets=[
                TargetMigration(
                    target='public.publications',
                    fields=[Copy('f_id')],
                ),
                TargetMigration(
                    target='public.publications_descriptions',
                    fields=[
                        Transform('f_description', lambda value, row: str(value).replace('\x01', '')),
                        Transform('f_id', lambda value, row: value, v2_name='f_publication'),
                    ],
                ),
                TargetMigration(
                    target='public.publications_relationship',
                    fields=[
                        Copy('f_status'),
                        Transform('f_process_id', lambda value, row: None if value == 0 else value, v2_name='f_process'),
                        Transform('f_id', lambda value, row: value, v2_name='f_publication'),
                    ],
                ),
            ],
        ).run(
            MigrationEngine(
                conn,
                writes.append,
                progress_fn=lambda table, count, total, done: progress.append((table, count, total, done)),
                progress_every=1,
            )
        )

        self.assertEqual(
            conn.cursor_calls[0].executed_sql,
            'SELECT COUNT(*) FROM (SELECT f_id, f_description, f_status, f_process_id FROM public.publications ) progress_src',
        )
        self.assertEqual(
            conn.cursor_calls[1].executed_sql,
            'SELECT f_id, f_description, f_status, f_process_id FROM public.publications ',
        )
        self.assertEqual(
            writes,
            [
                'INSERT INTO public.publications (f_id) VALUES (9);\n',
                "INSERT INTO public.publications_descriptions (f_description, f_publication) VALUES ('texto', 9);\n",
                'INSERT INTO public.publications_relationship (f_status, f_process, f_publication) VALUES (2, NULL, 9);\n',
            ],
        )
        self.assertEqual(
            progress,
            [
                ('public.publications', 0, 1, False),
                ('public.publications', 1, 1, False),
                ('public.publications', 1, 1, True),
            ],
        )


if __name__ == '__main__':
    unittest.main()
