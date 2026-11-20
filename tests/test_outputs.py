import json
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
from core.outputs import MirrorOutput, OutputAdapter, SqlFileOutput
from core.runner import MigrationRunner
from core.runtime import MigrationLogger, MigrationProgress


def read_file(path):
    with open(path, encoding='utf-8') as sql_file:
        return sql_file.read()


class RecordingOutput:
    """In-memory OutputAdapter that records every call in order."""

    def __init__(self, name='recording', log=None, fail_on=()):
        self.name = name
        self.events = []
        self.log = log
        self.fail_on = set(fail_on)
        self.closed = False

    def _record(self, *event):
        self.events.append(event)
        if self.log is not None:
            self.log.append((self.name, *event))
        if event[0] in self.fail_on:
            raise RuntimeError(f'{self.name} falhou em {event[0]}')

    def open_phase(self, phase):
        self._record('open_phase', phase)

    def write(self, sql):
        self._record('write', sql)

    def comment(self, text):
        self._record('comment', text)

    def flush(self):
        self._record('flush')

    def finish(self, success):
        self._record('finish', success)

    def describe(self):
        return {'type': self.name}

    def close(self):
        self.closed = True
        self._record('close')


class FailingMigration:
    """Composed migration whose load phase raises after writing SQL."""

    enable_indexes = ''

    def __init__(self, context):
        self.context = context

    def run_initial(self):
        self.context.output.write('-- prepare\n')

    def run_inserts(self):
        self.context.output.write('INSERT INTO public.demo VALUES (1);\n')
        raise RuntimeError('falha no insert')


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
        self.assertIs(core.MirrorOutput, MirrorOutput)
        self.assertIs(core.OutputAdapter, OutputAdapter)
        self.assertFalse(hasattr(core, 'SqlArtifactWriter'))

    def test_sql_file_output_implements_output_adapter_contract(self):
        for method in ('open_phase', 'write', 'comment', 'flush', 'finish', 'describe', 'close'):
            self.assertTrue(hasattr(OutputAdapter, method), method)
            self.assertTrue(callable(getattr(SqlFileOutput, method, None)), method)
            self.assertTrue(callable(getattr(MirrorOutput, method, None)), method)

    def test_finish_flushes_pending_sql_for_both_results(self):
        for success in (True, False):
            output = SqlFileOutput(self.artifacts)
            output.open_phase('load_transformed_data')
            file_writes = []
            real_write = output.file.write

            def recording_write(text, real_write=real_write, file_writes=file_writes):
                file_writes.append(text)
                return real_write(text)

            output.file.write = recording_write
            output.write(f'-- {success}\n')
            self.assertEqual(file_writes, [])

            output.finish(success)

            self.assertEqual(file_writes, [f'-- {success}\n'])
            output.close()
            self.assertEqual(read_file(self.artifacts.sql_path('load_transformed_data')), f'-- {success}\n')

    def test_describe_identifies_sql_file_output(self):
        self.assertEqual(SqlFileOutput(self.artifacts).describe(), {'type': 'sql-file'})


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
                ('finish', True),
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

        with open(context.artifacts.manifest_path, encoding='utf-8') as manifest_file:
            self.assertEqual(json.load(manifest_file)['output'], {'type': 'sql-file'})

    def _run(self, context, migration):
        with redirect_stdout(StringIO()):
            MigrationRunner(context, migration).run()
        context.close()
        with open(context.artifacts.manifest_path, encoding='utf-8') as manifest_file:
            return json.load(manifest_file)

    def test_failed_run_finishes_output_once_with_failure(self):
        output = RecordingOutput()
        context = self._context(output)

        manifest = self._run(context, FailingMigration(context))

        self.assertEqual([event for event in output.events if event[0] == 'finish'], [('finish', False)])
        self.assertEqual(output.events[-2:], [('finish', False), ('close',)])
        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(manifest['error'], 'falha no insert')

    def test_failing_finish_true_marks_run_failed_without_finish_false(self):
        output = RecordingOutput(fail_on={'finish'})
        context = self._context(output)

        class EmptyMigration:
            enable_indexes = ''

        manifest = self._run(context, EmptyMigration())

        self.assertEqual([event for event in output.events if event[0] == 'finish'], [('finish', True)])
        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(manifest['error'], 'recording falhou em finish')

    def test_failing_finish_false_is_logged_and_keeps_original_error(self):
        output = RecordingOutput(fail_on={'finish'})
        context = self._context(output)

        manifest = self._run(context, FailingMigration(context))

        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(manifest['error'], 'falha no insert')
        self.assertIn(
            '[ERRO] Falha ao finalizar o output: recording falhou em finish',
            read_file(context.artifacts.log_path('migration.log')),
        )

    def test_failed_run_keeps_partial_sql_artifacts(self):
        artifacts = ArtifactManager(self.clientdir)
        context = self._context(SqlFileOutput(artifacts))
        context.artifacts = artifacts

        manifest = self._run(context, FailingMigration(context))

        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(read_file(artifacts.sql_path('prepare_target')), '-- prepare\n')
        self.assertEqual(
            read_file(artifacts.sql_path('load_transformed_data')),
            'INSERT INTO public.demo VALUES (1);\n',
        )

    def test_manifest_records_output_description(self):
        output = MirrorOutput(RecordingOutput('a'), RecordingOutput('b'))
        context = self._context(output)

        class EmptyMigration:
            enable_indexes = ''

        manifest = self._run(context, EmptyMigration())

        self.assertEqual(manifest['status'], 'success')
        self.assertEqual(manifest['output'], {'type': 'mirror', 'outputs': [{'type': 'a'}, {'type': 'b'}]})

    def test_mirror_of_sql_file_outputs_writes_identical_artifacts(self):
        first = ArtifactManager(self.clientdir, timestamp='20260915-120000')
        second = ArtifactManager(self.clientdir, timestamp='20260915-120001')
        context = self._context(MirrorOutput(SqlFileOutput(first), SqlFileOutput(second)))

        class ComposedMigration:
            enable_indexes = 'REINDEX demo;\n'

            def __init__(self, context):
                self.context = context

            def run_initial(self):
                self.context.output.comment('PREPARACAO')
                self.context.output.write('DELETE FROM public.demo;\n')

            def run_inserts(self):
                for index in range(3):
                    self.context.output.write(f'INSERT INTO public.demo VALUES ({index});\n')

        manifest = self._run(context, ComposedMigration(context))

        self.assertEqual(manifest['status'], 'success')
        for phase in ('prepare_target', 'load_transformed_data', 'finalize_target'):
            self.assertEqual(read_file(first.sql_path(phase)), read_file(second.sql_path(phase)), phase)
        self.assertIn('DELETE FROM public.demo;\n', read_file(first.sql_path('prepare_target')))


class MirrorOutputTest(unittest.TestCase):
    def test_requires_at_least_one_output(self):
        with self.assertRaises(ValueError):
            MirrorOutput()

    def test_forwards_every_call_to_each_output_in_order(self):
        log = []
        first = RecordingOutput('first', log)
        second = RecordingOutput('second', log)
        output = MirrorOutput(first, second)

        output.open_phase('prepare_target')
        output.write('DELETE FROM demo;\n')
        output.comment('SECAO')
        output.flush()
        output.finish(True)
        output.close()

        calls = [
            ('open_phase', 'prepare_target'),
            ('write', 'DELETE FROM demo;\n'),
            ('comment', 'SECAO'),
            ('flush',),
            ('finish', True),
            ('close',),
        ]
        self.assertEqual(first.events, calls)
        self.assertEqual(second.events, calls)
        self.assertEqual(
            log,
            [entry for call in calls for entry in (('first', *call), ('second', *call))],
        )

    def test_write_stops_at_first_error(self):
        first = RecordingOutput('first', fail_on={'write'})
        second = RecordingOutput('second')
        output = MirrorOutput(first, second)

        with self.assertRaisesRegex(RuntimeError, 'first falhou em write'):
            output.write('INSERT INTO demo VALUES (1);\n')

        self.assertEqual(second.events, [])

    def test_finish_and_close_reach_every_output_and_raise_first_error(self):
        for method, args in (('finish', (False,)), ('close', ())):
            first = RecordingOutput('first', fail_on={method})
            second = RecordingOutput('second', fail_on={method})
            third = RecordingOutput('third')
            output = MirrorOutput(first, second, third)

            with self.assertRaisesRegex(RuntimeError, f'first falhou em {method}'):
                getattr(output, method)(*args)

            for child in (first, second, third):
                self.assertEqual(child.events, [(method, *args)], (method, child.name))

    def test_describe_lists_wrapped_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = ArtifactManager(os.path.join(tmpdir, 'Acme'))
            output = MirrorOutput(SqlFileOutput(artifacts), SqlFileOutput(artifacts))

            self.assertEqual(
                output.describe(),
                {'type': 'mirror', 'outputs': [{'type': 'sql-file'}, {'type': 'sql-file'}]},
            )


if __name__ == '__main__':
    unittest.main()
