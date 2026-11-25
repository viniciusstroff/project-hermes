import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO

# Mesmos stubs dos outros testes: o primeiro módulo importado na descoberta
# define o stub de todos, então ele precisa servir para todos.
psycopg2 = types.ModuleType('psycopg2')
psycopg2_extras = types.ModuleType('psycopg2.extras')
psycopg2_extras.RealDictCursor = object
psycopg2.extras = psycopg2_extras
psycopg2.connect = lambda *args, **kwargs: None
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)

firebirdsql = types.ModuleType('firebirdsql')
firebirdsql.connect = lambda *args, **kwargs: None
sys.modules.setdefault('firebirdsql', firebirdsql)


def dotenv_values(path):
    values = {}
    with open(path, encoding='utf-8') as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key] = value
    return values


dotenv = types.ModuleType('dotenv')
dotenv.dotenv_values = dotenv_values
sys.modules.setdefault('dotenv', dotenv)

from core.adapters.postgres import PostgresTargetAdapter
from core.artifacts import ArtifactManager
from core.context import MigrationContext
from core.dialects import PostgresDialect
from core.migration_engine import MigrationEngine, ValueSerializationError
from core.multi_target_migration import MultiTargetMigration, TargetMigration
from core.runner import MigrationRunner
from core.runtime import MigrationLogger, MigrationProgress
from core.strategies import Copy, Rename
from core.table_migration import TableMigration

SECRET = 'cpf 123.456.789-00'


class FakeSource:
    def __init__(self, rows):
        self.dialect = PostgresDialect()
        self.rows = rows

    def limit_clause(self, limit):
        return limit or ''

    def scalar(self, sql):
        return len(self.rows)

    def cursor(self):
        source = self

        class Cursor:
            def execute(self, sql):
                pass

            def __iter__(self):
                return iter(source.rows)

        return Cursor()


class ListOutput:
    def __init__(self):
        self.written = []

    def open_phase(self, phase):
        pass

    def write(self, sql):
        self.written.append(sql)

    def comment(self, text):
        pass

    def flush(self):
        pass

    def finish(self, success):
        pass

    def describe(self):
        return {'type': 'list'}

    def close(self):
        pass


def engine_for(rows, warn_fn=None):
    writes = []
    engine = MigrationEngine(FakeSource(rows), PostgresTargetAdapter(), writes.append, warn_fn=warn_fn)
    return engine, writes


class SerializationErrorTest(unittest.TestCase):
    def test_nul_error_names_table_column_and_position_without_the_value(self):
        engine, writes = engine_for([{'f_id': 1, 'f_text': SECRET + '\x00'}])
        migration = TableMigration('SELECT {fields} FROM notas {limit}', 'public.notes', [Copy('f_id'), Copy('f_text')])

        with self.assertRaises(ValueSerializationError) as raised:
            migration.run(engine)

        error = raised.exception
        self.assertEqual((error.table, error.column, error.python_type), ('public.notes', 'f_text', 'str'))
        message = str(error)
        self.assertIn('public.notes.f_text', message)
        self.assertIn(f'position {len(SECRET)}', message)
        self.assertNotIn('123.456', message)
        self.assertEqual(writes, [])

    def test_unsupported_type_in_multi_target_names_target_and_type(self):
        engine, _ = engine_for([{'f_id': 1, 'f_tags': ['a', 'b']}])
        migration = MultiTargetMigration('SELECT {fields} FROM t {limit}', [
            TargetMigration('public.items', [Copy('f_id')]),
            TargetMigration('public.item_tags', [Rename('f_id', 'f_item'), Copy('f_tags')]),
        ])

        with self.assertRaises(ValueSerializationError) as raised:
            migration.run(engine)

        self.assertEqual(str(raised.exception).split(':')[0], 'public.item_tags.f_tags (list)')
        self.assertNotIn("'a'", str(raised.exception))


class BinaryWarningTest(unittest.TestCase):
    def test_binary_column_is_reported_once(self):
        warnings = []
        seen = set()

        def warn_once(key, message):
            if key not in seen:
                seen.add(key)
                warnings.append(message)

        rows = [{'f_id': number, 'f_content': bytes([number])} for number in range(1, 4)]
        engine, writes = engine_for(rows, warn_fn=warn_once)

        TableMigration('SELECT {fields} FROM docs {limit}', 'public.documents',
                       [Copy('f_id'), Copy('f_content')]).run(engine)

        self.assertEqual(len(writes), 3)
        self.assertIn("decode('01', 'hex')", writes[0])
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith('public.documents.f_content recebeu valor binário'))


class RunnerFailureTest(unittest.TestCase):
    def test_rejected_value_fails_the_run_with_the_migration_error(self):
        class NotesMigration:
            def __init__(self, context):
                self.context = context

            def set_trigger_commands(self):
                self.disable_triggers = self.enable_triggers = ''

            def run_inserts(self):
                TableMigration('SELECT {fields} FROM notas {limit}', 'public.notes',
                               [Copy('f_text')]).run(self.context.source('v1'))

        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = os.path.join(tmpdir, 'Acme')
            os.makedirs(clientdir)
            artifacts = ArtifactManager(clientdir)
            logger = MigrationLogger(artifacts)
            progress = MigrationProgress(logger)
            logger.progress = progress
            context = MigrationContext(
                clientdir=clientdir,
                config={},
                artifacts=artifacts,
                sources={'v1': FakeSource([{'f_text': SECRET + '\x00'}])},
                target=PostgresTargetAdapter(),
                metadata=None,
                output=ListOutput(),
                logger=logger,
                progress=progress,
            )

            with redirect_stdout(StringIO()):
                MigrationRunner(context, NotesMigration(context)).run()
            context.close()
            with open(artifacts.manifest_path, encoding='utf-8') as manifest_file:
                manifest = json.load(manifest_file)
            with open(artifacts.log_path('migration.log'), encoding='utf-8') as log_file:
                log = log_file.read()

        self.assertEqual(manifest['status'], 'failed')
        self.assertTrue(manifest['error'].startswith('public.notes.f_text (str): text has a NUL character'))
        self.assertNotIn('123.456', manifest['error'])
        self.assertNotIn('123.456', log)

    def test_context_warns_once_per_key(self):
        lines = []
        context = MigrationContext(
            clientdir='x', config={}, artifacts=None, sources={}, target=None, metadata=None,
            output=None, logger=types.SimpleNamespace(print_log=lines.append), progress=None,
        )

        context.warn_once('binary:public.documents.f_content', 'mensagem')
        context.warn_once('binary:public.documents.f_content', 'mensagem')
        context.warn_once('binary:public.documents.f_other', 'outra')

        self.assertEqual(lines, ['[AVISO] mensagem', '[AVISO] outra'])


if __name__ == '__main__':
    unittest.main()
