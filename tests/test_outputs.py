import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

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

dotenv = types.ModuleType('dotenv')


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


dotenv.dotenv_values = dotenv_values
sys.modules.setdefault('dotenv', dotenv)

import core
from core.artifacts import ArtifactManager
from core.context import DefaultMigrationContextFactory, MigrationContext
from core.outputs import OutputAdapter, SqlFileOutput
from core.runner import MigrationRunner
from core.runtime import MigrationLogger, MigrationProgress


def read_file(path):
    with open(path, encoding='utf-8') as sql_file:
        return sql_file.read()


class RecordingOutput:
    """In-memory OutputAdapter that records every call in order."""

    def __init__(self):
        self.events = []
        self.closed = False

    def open_phase(self, phase):
        self.events.append(('open_phase', phase))

    def write(self, sql):
        self.events.append(('write', sql))

    def comment(self, text):
        self.events.append(('comment', text))

    def flush(self):
        self.events.append(('flush',))

    def close(self):
        self.events.append(('close',))
        self.closed = True


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql):
        self.connection.executed.append(sql)

    def fetchone(self):
        return self.connection.rows[0] if self.connection.rows else None

    def __iter__(self):
        return iter(self.connection.rows)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []
        self.closed = False

    def cursor(self, *args, **kwargs):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class FakeMetadata:
    def disable_triggers_sql(self):
        return 'ALTER TABLE public.demo DISABLE TRIGGER ALL;\n'

    def enable_triggers_sql(self):
        return 'ALTER TABLE public.demo ENABLE TRIGGER ALL;\n'


ENV_LINES = [
    'PG_V1_HOST=source-db',
    'PG_V1_PORT=5432',
    'PG_V1_NAME=source',
    'PG_V1_USER=source-user',
    'PG_V1_PASS=source-pass',
    'PG_V2_HOST=target-db',
    'PG_V2_PORT=5432',
    'PG_V2_NAME=target',
    'PG_V2_USER=target-user',
    'PG_V2_PASS=target-pass',
    'FB_V1_HOST=firebird-db',
    'FB_V1_PORT=3050',
    'FB_V1_NAME=firebird',
    'FB_V1_USER=firebird-user',
    'FB_V1_PASS=firebird-pass',
    'FB_V1_CHARSET=ISO8859_1',
]


class SqlFileOutputTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        clientdir = os.path.join(self._tmpdir.name, 'Acme')
        os.makedirs(clientdir)
        self.artifacts = ArtifactManager(clientdir, timestamp='20260915-120000')

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_open_phase_maps_logical_keys_to_existing_artifact_files(self):
        output = SqlFileOutput(self.artifacts)
        expected = {
            'setup': '00_setup.sql',
            'prepare_target': '01_prepare_target.sql',
            'load_transformed_data': '03_load_transformed_data.sql',
            'finalize_target': '04_finalize_target.sql',
        }

        for phase, file_name in expected.items():
            output.open_phase(phase)
            self.assertEqual(output.file.name, os.path.join(self.artifacts.sql_dir, file_name))
            self.assertTrue(os.path.exists(output.file.name))

        output.close()

    def test_writes_are_buffered_until_buffer_size_or_flush(self):
        output = SqlFileOutput(self.artifacts, buffer_size=3)
        output.open_phase('prepare_target')
        file_writes = []
        real_write = output.file.write

        def recording_write(text):
            file_writes.append(text)
            return real_write(text)

        output.file.write = recording_write

        output.write('a;\n')
        output.write('b;\n')
        self.assertEqual(file_writes, [])

        output.write('c;\n')
        self.assertEqual(file_writes, ['a;\nb;\nc;\n'])

        output.write('d;\n')
        self.assertEqual(file_writes, ['a;\nb;\nc;\n'])
        output.flush()
        self.assertEqual(file_writes, ['a;\nb;\nc;\n', 'd;\n'])

        output.close()
        self.assertEqual(read_file(self.artifacts.sql_path('prepare_target')), 'a;\nb;\nc;\nd;\n')

    def test_comment_renders_current_sql_comment_block(self):
        output = SqlFileOutput(self.artifacts)
        output.open_phase('finalize_target')

        output.comment('HABILITAR ÍNDICES')
        output.close()

        self.assertEqual(
            read_file(self.artifacts.sql_path('finalize_target')),
            '\n-----' + '-' * 17 + '\n-----HABILITAR ÍNDICES\n-----' + '-' * 17 + '\n\n',
        )

    def test_opening_next_phase_flushes_previous_phase(self):
        output = SqlFileOutput(self.artifacts)
        output.open_phase('prepare_target')
        output.write('-- prepare\n')

        output.open_phase('load_transformed_data')
        output.write('-- load\n')
        output.close()

        self.assertEqual(read_file(self.artifacts.sql_path('prepare_target')), '-- prepare\n')
        self.assertEqual(read_file(self.artifacts.sql_path('load_transformed_data')), '-- load\n')

    def test_close_flushes_releases_file_and_is_idempotent(self):
        output = SqlFileOutput(self.artifacts)
        output.open_phase('setup')
        handle = output.file
        output.write('-- setup\n')

        output.close()
        output.close()

        self.assertTrue(handle.closed)
        self.assertIsNone(output.file)
        self.assertEqual(read_file(self.artifacts.sql_path('setup')), '-- setup\n')

    def test_core_package_exports_output_types(self):
        self.assertIs(core.SqlFileOutput, SqlFileOutput)
        self.assertIs(core.OutputAdapter, OutputAdapter)
        self.assertFalse(hasattr(core, 'SqlArtifactWriter'))

    def test_sql_file_output_implements_output_adapter_contract(self):
        for method in ('open_phase', 'write', 'comment', 'flush', 'close'):
            self.assertTrue(hasattr(OutputAdapter, method), method)
            self.assertTrue(callable(getattr(SqlFileOutput, method, None)), method)


class OutputAdapterIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.clientdir = os.path.join(self._tmpdir.name, 'Acme')
        os.makedirs(self.clientdir)
        with open(os.path.join(self.clientdir, '.env'), 'w', encoding='utf-8') as env_file:
            env_file.write('\n'.join(ENV_LINES))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _context(self, output, source=None):
        artifacts = ArtifactManager(self.clientdir)
        logger = MigrationLogger(artifacts)
        progress = MigrationProgress(logger)
        logger.progress = progress
        return MigrationContext(
            clientdir=self.clientdir,
            config={},
            artifacts=artifacts,
            sources={'pg_v1': source},
            target=None,
            metadata=FakeMetadata(),
            output=output,
            logger=logger,
            progress=progress,
        )

    def test_context_exposes_output_as_only_output_api(self):
        output = RecordingOutput()
        context = self._context(output)

        self.assertIs(context.output, output)
        self.assertFalse(hasattr(context, 'writer'))
        context.close()

    def test_context_engine_writes_through_configured_output(self):
        output = RecordingOutput()
        context = self._context(output, source=object())

        context.engine('pg_v1').write_sql('INSERT INTO demo VALUES (1);\n')

        self.assertEqual(output.events, [('write', 'INSERT INTO demo VALUES (1);\n')])
        context.close()

    def test_context_close_closes_configured_output_once(self):
        output = RecordingOutput()
        context = self._context(output)

        context.close()
        context.close()

        self.assertTrue(output.closed)
        self.assertEqual(output.events.count(('close',)), 1)

    def test_runner_drives_phases_through_configured_output(self):
        output = RecordingOutput()
        context = self._context(output)

        class ComposedMigration:
            enable_indexes = 'REINDEX demo;\n'

            def __init__(self, context):
                self.context = context

            def run_initial(self):
                self.context.output.write('-- prepare\n')

            def run_inserts(self):
                self.context.output.write('-- load\n')

            def reset_sequences(self):
                self.context.output.write('-- finalize\n')

        with redirect_stdout(StringIO()):
            MigrationRunner(context, ComposedMigration(context)).run()
        context.close()

        self.assertEqual(
            output.events,
            [
                ('open_phase', 'prepare_target'),
                ('write', '-- prepare\n'),
                ('open_phase', 'load_transformed_data'),
                ('write', '-- load\n'),
                ('open_phase', 'finalize_target'),
                ('write', '-- finalize\n'),
                ('comment', 'HABILITAR ÍNDICES DE TABELAS'),
                ('write', 'REINDEX demo;\n'),
                ('comment', 'HABILITAR TRIGGERS DE TABELAS'),
                ('write', 'ALTER TABLE public.demo ENABLE TRIGGER ALL;\n'),
                ('flush',),
                ('close',),
            ],
        )

    def test_default_factory_builds_sql_file_output_with_setup_phase_open(self):
        with patch('core.context.psycopg2.connect', side_effect=[FakeConnection(), FakeConnection()]), \
             patch('core.context.firebirdsql.connect', return_value=FakeConnection()):
            with redirect_stdout(StringIO()):
                context = DefaultMigrationContextFactory.from_clientdir(self.clientdir)

        self.assertIsInstance(context.output, SqlFileOutput)
        self.assertEqual(context.output.file.name, context.artifacts.sql_path('setup'))

        context.close()
        self.assertIsNone(context.output.file)

    def test_default_output_writes_artifacts_without_executing_sql_on_target(self):
        pg_v1 = FakeConnection()
        pg_v2 = FakeConnection(rows=[
            ('ALTER TABLE public.demo DISABLE TRIGGER ALL;', 'ALTER TABLE public.demo ENABLE TRIGGER ALL;'),
        ])
        fb_v1 = FakeConnection()

        class ComposedMigration:
            enable_indexes = ''

            def __init__(self, context):
                self.context = context

            def run_initial(self):
                self.context.output.write('DELETE FROM public.demo;\n')

            def run_inserts(self):
                self.context.output.write('INSERT INTO public.demo VALUES (1);\n')

        with patch('core.context.psycopg2.connect', side_effect=[pg_v1, pg_v2]), \
             patch('core.context.firebirdsql.connect', return_value=fb_v1):
            with redirect_stdout(StringIO()):
                context = DefaultMigrationContextFactory.from_clientdir(self.clientdir)
                MigrationRunner(context, ComposedMigration(context)).run()
        context.close()

        self.assertEqual(
            read_file(context.artifacts.sql_path('prepare_target')),
            'DELETE FROM public.demo;\n',
        )
        self.assertEqual(
            read_file(context.artifacts.sql_path('load_transformed_data')),
            'INSERT INTO public.demo VALUES (1);\n',
        )
        executed = pg_v1.executed + pg_v2.executed + fb_v1.executed
        self.assertNotIn('DELETE FROM public.demo;\n', executed)
        self.assertNotIn('INSERT INTO public.demo VALUES (1);\n', executed)
        self.assertTrue(all(sql.startswith('SELECT') for sql in executed))


if __name__ == '__main__':
    unittest.main()
