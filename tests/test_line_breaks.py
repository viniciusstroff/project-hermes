"""Carriage returns inside values reach the artifact and the target unchanged."""

import os
import sys
import tempfile
import types
import unittest

# Mesmos stubs dos outros testes: o primeiro módulo importado na descoberta
# define o stub de todos, então ele precisa servir para todos.
psycopg2 = types.ModuleType('psycopg2')
psycopg2_extras = types.ModuleType('psycopg2.extras')
psycopg2_extras.RealDictCursor = object
psycopg2.extras = psycopg2_extras
psycopg2.connect = lambda *args, **kwargs: None
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)

from core.adapters.postgres import PostgresTargetAdapter
from core.adapters.postgres_session import PostgresSession
from core.adapters.postgres_splitter import split_sql_file
from core.apply import DRY_RUN, ArtifactApplier
from core.artifacts import ArtifactManager
from core.operations import Insert
from core.outputs import FileOutput, MirrorOutput
from core.outputs.direct import DirectDatabaseOutput, _lines
from tests.apply_fakes import FakeConnection

TARGET = PostgresTargetAdapter()
TEXTS = ['linha 1\r\nlinha 2', 'antes\rdepois', 'só\nLF']
DUMP = "INSERT INTO public.holidays VALUES (1, 'Natal\r\nrecesso');\nINSERT INTO public.holidays VALUES (2, 'a\rb');\n"


class LineBreakTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.artifacts = ArtifactManager(os.path.join(self._tmpdir.name, 'Acme'), timestamp='20261126-100000')

    def tearDown(self):
        self._tmpdir.cleanup()

    def dump_file(self):
        path = os.path.join(self._tmpdir.name, 'v1.pg_dump.tmp')
        with open(path, 'w', encoding='utf-8', newline='') as dump:
            dump.write(DUMP)
        return path

    def phase_bytes(self, phase):
        with open(self.artifacts.command_path(phase, 'sql'), 'rb') as artifact:
            return artifact.read()

    def write_run(self, operations, include=None):
        output = FileOutput(self.artifacts, TARGET)
        output.open_phase('load_transformed_data')
        for operation in operations:
            output.write(operation)
        if include:
            output.include_file(include)
        output.finish(True)
        output.close()
        self.artifacts.write_manifest(status='success', connections={'target': {'adapter': 'postgres'}})


class FileAndApplierTest(LineBreakTestCase):
    def test_values_reach_the_artifact_and_the_target_unchanged(self):
        self.write_run([Insert('notes', {'id': number, 'body': text}) for number, text in enumerate(TEXTS, 1)])
        connection = FakeConnection()

        ArtifactApplier(self.artifacts.run_dir, lambda: PostgresSession(connection), mode=DRY_RUN).apply()

        self.assertIn(b"'linha 1\r\nlinha 2'", self.phase_bytes('load_transformed_data'))
        executed = [sql for sql in connection.log if sql.startswith('INSERT')]
        self.assertEqual(executed, [
            f"INSERT INTO notes (id, body) VALUES ({number}, '{text}')" for number, text in enumerate(TEXTS, 1)
        ])

    def test_included_file_is_copied_byte_for_byte(self):
        self.write_run([], include=self.dump_file())

        self.assertEqual(self.phase_bytes('load_transformed_data'), DUMP.encode('utf-8'))

    def test_comment_ends_at_a_carriage_return(self):
        path = os.path.join(self._tmpdir.name, 'x.sql')
        with open(path, 'wb') as sql_file:
            sql_file.write("-- comentário\rINSERT INTO a VALUES ('x\ry');\n".encode('utf-8'))

        self.assertEqual([statement.text for statement in split_sql_file(path)], ["INSERT INTO a VALUES ('x\ry')"])


class DirectOutputTest(LineBreakTestCase):
    def output(self, connection):
        return DirectDatabaseOutput(TARGET, lambda: PostgresSession(connection), self.artifacts.run_dir, DRY_RUN)

    def test_written_value_keeps_its_carriage_return(self):
        connection = FakeConnection()
        output = self.output(connection)
        output.open_phase('load_transformed_data')
        output.write(Insert('notes', {'id': 1, 'body': TEXTS[0]}))
        output.finish(True)

        self.assertIn("INSERT INTO notes (id, body) VALUES (1, 'linha 1\r\nlinha 2')", connection.log)

    def test_included_file_keeps_its_carriage_returns(self):
        connection = FakeConnection()
        output = self.output(connection)
        output.open_phase('copy_compatible_entities')
        output.include_file(self.dump_file())
        output.finish(True)

        self.assertIn("INSERT INTO public.holidays VALUES (1, 'Natal\r\nrecesso')", connection.log)
        self.assertIn("INSERT INTO public.holidays VALUES (2, 'a\rb')", connection.log)

    def test_rendered_text_is_split_as_a_file(self):
        text = "INSERT INTO a VALUES ('x y\x85z', 'w\r\nv');\n-- fim\n"
        path = os.path.join(self._tmpdir.name, 'x.sql')
        with open(path, 'w', encoding='utf-8', newline='') as sql_file:
            sql_file.write(text)
        with open(path, encoding='utf-8', newline='') as sql_file:
            self.assertEqual(_lines(text), list(sql_file))

    def test_hybrid_runs_the_same_commands_as_the_applier(self):
        operations = [Insert('notes', {'id': number, 'body': text}) for number, text in enumerate(TEXTS, 1)]
        direct_connection = FakeConnection()
        output = MirrorOutput(FileOutput(self.artifacts, TARGET), self.output(direct_connection))
        output.open_phase('load_transformed_data')
        for operation in operations:
            output.write(operation)
        output.include_file(self.dump_file())
        output.finish(True)
        output.close()
        self.artifacts.write_manifest(status='success', connections={'target': {'adapter': 'postgres'}})

        applier_connection = FakeConnection()
        ArtifactApplier(self.artifacts.run_dir, lambda: PostgresSession(applier_connection), mode=DRY_RUN).apply()

        self.assertEqual(direct_connection.log, applier_connection.log)
        self.assertTrue(any('\r\n' in sql for sql in applier_connection.log))


if __name__ == '__main__':
    unittest.main()
