import unittest
import sys
import types
from pathlib import Path

psycopg2 = types.ModuleType('psycopg2')
psycopg2_extras = types.ModuleType('psycopg2.extras')
psycopg2_extras.RealDictCursor = object
psycopg2.extras = psycopg2_extras
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)

from core.adapters import PostgresSourceAdapter, PostgresTargetAdapter
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


def make_engine(conn, writer, **kwargs):
    return MigrationEngine(
        PostgresSourceAdapter(conn, cursor_factory=object),
        PostgresTargetAdapter(),
        writer,
        **kwargs,
    )


class TableMigrationProgressTest(unittest.TestCase):
    def test_engine_has_no_direct_driver_or_firebird_branch(self):
        engine_source = Path('core/migration_engine.py').read_text(encoding='utf-8')

        self.assertNotIn('psycopg2', engine_source)
        self.assertNotIn("source == 'firebird'", engine_source)

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

        engine = make_engine(
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
                'f_plan_type': 2,
                'f_days': 3,
                'f_requires_approval': True,
                'f_maximum_items': 4,
                'f_status': 1,
            }
        ])
        writes = []
        progress = []

        ExpandMigration(
            source_sql='SELECT {fields} FROM public.acme_schedule_rules_demo {limit}',
            target='public.acme_schedule_rules_demo',
            fields=[
                Copy('f_plan_type'),
                Copy('f_days'),
                Copy('f_requires_approval'),
                Copy('f_maximum_items'),
                Copy('f_status'),
            ],
            first_fields=[
                Copy('f_id'),
                Copy('f_plan_type'),
                Copy('f_days'),
                Copy('f_requires_approval'),
                Copy('f_maximum_items'),
                Copy('f_status'),
            ],
            expand_col='f_region',
            expand_values=[1, 2, 3],
        ).run(
            make_engine(
                conn,
                writes.append,
                progress_fn=lambda table, count, total, done: progress.append((table, count, total, done)),
                progress_every=2,
            )
        )

        self.assertEqual(
            conn.cursor_calls[0].executed_sql,
            'SELECT COUNT(*) FROM (SELECT f_id, f_plan_type, f_days, f_requires_approval, f_maximum_items, f_status FROM public.acme_schedule_rules_demo ) progress_src',
        )
        self.assertEqual(
            conn.cursor_calls[1].executed_sql,
            'SELECT f_id, f_plan_type, f_days, f_requires_approval, f_maximum_items, f_status FROM public.acme_schedule_rules_demo ',
        )
        self.assertEqual(
            writes,
            [
                'INSERT INTO public.acme_schedule_rules_demo (f_id, f_plan_type, f_days, f_requires_approval, f_maximum_items, f_status, f_region) VALUES (10, 2, 3, true, 4, 1, 1);\n',
                'INSERT INTO public.acme_schedule_rules_demo (f_plan_type, f_days, f_requires_approval, f_maximum_items, f_status, f_region) VALUES (2, 3, true, 4, 1, 2);\n',
                'INSERT INTO public.acme_schedule_rules_demo (f_plan_type, f_days, f_requires_approval, f_maximum_items, f_status, f_region) VALUES (2, 3, true, 4, 1, 3);\n',
            ],
        )
        self.assertEqual(
            progress,
            [
                ('public.acme_schedule_rules_demo', 0, 3, False),
                ('public.acme_schedule_rules_demo', 2, 3, False),
                ('public.acme_schedule_rules_demo', 3, 3, True),
            ],
        )

    def test_does_not_emit_progress_for_empty_result(self):
        conn = FakeConnection([])
        progress = []

        engine = make_engine(
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
        ).run(make_engine(conn, lambda sql: None))

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
                'f_order_id': 0,
                'f_status': 2,
            }
        ])
        writes = []
        progress = []

        MultiTargetMigration(
            source_sql='SELECT {fields} FROM public.acme_messages_demo {limit}',
            label='public.acme_messages_demo',
            targets=[
                TargetMigration(
                    target='public.acme_messages_demo',
                    fields=[Copy('f_id')],
                ),
                TargetMigration(
                    target='public.acme_message_bodies_demo',
                    fields=[
                        Transform('f_description', lambda value, row: str(value).replace('\x01', '')),
                        Transform('f_id', lambda value, row: value, v2_name='f_message'),
                    ],
                ),
                TargetMigration(
                    target='public.acme_message_links_demo',
                    fields=[
                        Copy('f_status'),
                        Transform('f_order_id', lambda value, row: None if value == 0 else value, v2_name='f_order'),
                        Transform('f_id', lambda value, row: value, v2_name='f_message'),
                    ],
                ),
            ],
        ).run(
            make_engine(
                conn,
                writes.append,
                progress_fn=lambda table, count, total, done: progress.append((table, count, total, done)),
                progress_every=1,
            )
        )

        self.assertEqual(
            conn.cursor_calls[0].executed_sql,
            'SELECT COUNT(*) FROM (SELECT f_id, f_description, f_status, f_order_id FROM public.acme_messages_demo ) progress_src',
        )
        self.assertEqual(
            conn.cursor_calls[1].executed_sql,
            'SELECT f_id, f_description, f_status, f_order_id FROM public.acme_messages_demo ',
        )
        self.assertEqual(
            writes,
            [
                'INSERT INTO public.acme_messages_demo (f_id) VALUES (9);\n',
                "INSERT INTO public.acme_message_bodies_demo (f_description, f_message) VALUES ('texto', 9);\n",
                'INSERT INTO public.acme_message_links_demo (f_status, f_order, f_message) VALUES (2, NULL, 9);\n',
            ],
        )
        self.assertEqual(
            progress,
            [
                ('public.acme_messages_demo', 0, 1, False),
                ('public.acme_messages_demo', 1, 1, False),
                ('public.acme_messages_demo', 1, 1, True),
            ],
        )


if __name__ == '__main__':
    unittest.main()
