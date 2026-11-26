import io
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

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

import Migracao
from core.adapters.postgres import PostgresTargetAdapter
from core.adapters.postgres_session import PostgresSession
from core.application import ArtifactReadError, Capabilities, CapabilityError, Command, DeferredCommandError
from core.apply import APPLY, CONTINUE, DRY_RUN, STOP, ArtifactApplier, CommitFailedError, StatementFailedError
from core.artifacts import ArtifactManager
from core.context import (
    DIRECT_OUTPUT,
    FILE_OUTPUT,
    HYBRID_OUTPUT,
    DefaultMigrationContextFactory,
    OutputSettings,
    _OUTPUT_SETTINGS,
    output_settings,
)
from core.operations import Clear, Comment, Insert, Native, ResetIdentity
from core.outputs import FileOutput, MirrorOutput
from core.outputs.direct import DirectDatabaseOutput, find_direct_output
from tests.apply_fakes import FakeConnection

TARGET = PostgresTargetAdapter()
LOAD = '03_load_transformed_data.sql'


class RecordingOpener:
    """Opens a PostgreSQL session over a fake connection and counts the openings."""

    def __init__(self, connection, waits_between_commands=False):
        self.connection = connection
        self.waits = waits_between_commands
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return PostgresSession(self.connection, waits_between_commands=self.waits)


class DirectOutputTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.run_dir = self._tmpdir.name
        self.messages = []

    def tearDown(self):
        self._tmpdir.cleanup()

    def output(self, connection, mode=APPLY, on_error=None, target=TARGET, opener=None, **options):
        self.opener = opener or RecordingOpener(connection)
        return DirectDatabaseOutput(
            target, self.opener, self.run_dir, mode, on_error, log=self.messages.append, **options,
        )

    def relatorio(self, output):
        with open(output.report.report_path, encoding='utf-8') as report_file:
            return json.load(report_file)


class DirectOutputTest(DirectOutputTestCase):
    def test_command_runs_before_write_returns(self):
        connection = FakeConnection()
        output = self.output(connection)
        output.open_phase('setup')
        output.open_phase('prepare_target')

        output.write(Insert('students', {'id': 1}))

        self.assertEqual(connection.pending, ['INSERT INTO students (id) VALUES (1)'])

    def test_setup_waits_until_prepare_target(self):
        connection = FakeConnection()
        output = self.output(connection)
        output.open_phase('setup')
        output.write(Native('postgres', 'SELECT 1;'))

        self.assertEqual(self.opener.calls, 0)
        self.assertEqual(connection.log, [])

        output.open_phase('prepare_target')
        output.write(Clear(('students',)))

        self.assertEqual(self.opener.calls, 1)
        self.assertEqual(connection.statements(), ['SELECT 1', 'RESET ALL', 'TRUNCATE students RESTART IDENTITY'])

    def test_phase_change_resets_session_settings(self):
        connection = FakeConnection()
        output = self.output(connection)
        output.open_phase('prepare_target')
        output.write(Native('postgres', "SELECT pg_catalog.set_config('search_path', '', false);"))
        output.open_phase('load_transformed_data')
        output.write(Insert('students', {'id': 1}))

        statements = connection.statements()
        self.assertLess(statements.index('RESET ALL'), statements.index('INSERT INTO students (id) VALUES (1)'))

    def test_lines_follow_the_phase_file(self):
        operations = [Comment('CARGA'), Insert('students', {'id': 1}), Insert('students', {'id': 2})]
        connection = FakeConnection(failures={'VALUES (2)': ('duplicate key', '23505')})
        output = self.output(connection, mode=DRY_RUN, on_error=CONTINUE)
        output.open_phase('load_transformed_data')
        for operation in operations:
            output.write(operation)
        output.finish(True)

        file_text = ''.join(line for operation in operations for line in TARGET.render(operation))
        expected_line = file_text.splitlines().index('INSERT INTO students (id) VALUES (2);') + 1
        with open(output.report.errors_path, encoding='utf-8') as errors:
            self.assertIn(f'== {LOAD}, comando 2, linha {expected_line}, código 23505', errors.read())

    def test_included_file_follows_the_applier_rules(self):
        dump = os.path.join(self.run_dir, 'v1.pg_dump.tmp')
        with open(dump, 'w', encoding='utf-8') as dump_file:
            dump_file.write("SELECT pg_catalog.set_config('search_path', '', false);\n"
                            'BEGIN;\nINSERT INTO public.holidays VALUES (1);\nCOMMIT;\n')
        connection = FakeConnection()
        output = self.output(connection)
        output.open_phase('copy_compatible_entities')
        output.write(Insert('feriados', {'id': 9}))
        output.include_file(dump)
        output.open_phase('load_transformed_data')
        output.write(Insert('students', {'id': 1}))
        output.finish(True)

        statements = connection.statements()
        self.assertNotIn('BEGIN', statements)
        self.assertLess(statements.index('INSERT INTO public.holidays VALUES (1)'), statements.index('RESET ALL'))
        self.assertLess(statements.index('RESET ALL'), statements.index('INSERT INTO students (id) VALUES (1)'))
        copy = self.relatorio(output)['files'][0]
        self.assertEqual(copy['file'], '02_copy_compatible_entities.sql')
        self.assertEqual(copy['skipped'], {'transaction_control': 2})

    def test_apply_commits(self):
        connection = FakeConnection()
        output = self.output(connection)
        output.open_phase('prepare_target')
        output.write(Insert('students', {'id': 1}))
        output.finish(True)
        output.close()

        self.assertEqual(connection.committed, ['INSERT INTO students (id) VALUES (1)'])
        self.assertEqual(self.relatorio(output)['result'], 'committed')
        self.assertTrue(connection.closed)

    def test_dry_run_rolls_back_and_skips_setval(self):
        connection = FakeConnection()
        output = self.output(connection, mode=DRY_RUN)
        output.open_phase('finalize_target')
        output.write(ResetIdentity('students', 'id'))
        output.finish(True)

        self.assertEqual(connection.log[-1], '<rollback>')
        self.assertEqual(connection.committed, [])
        report = self.relatorio(output)
        self.assertEqual((report['result'], report['on_error']), ('rolled_back', CONTINUE))
        self.assertEqual(report['files'][0]['skipped'], {'sequence_change': 1})

    def test_stop_policy_fails_the_write(self):
        connection = FakeConnection(failures={'VALUES (2)': ('duplicate key', '23505')})
        output = self.output(connection)
        output.open_phase('load_transformed_data')
        output.write(Insert('students', {'id': 1}))

        with self.assertRaises(StatementFailedError) as raised:
            output.write(Insert('students', {'id': 2}))
        output.finish(False)

        self.assertIn(f'{LOAD}, comando 2, linha 2', str(raised.exception))
        self.assertEqual(connection.log[-1], '<rollback>')
        report = self.relatorio(output)
        self.assertEqual(report['result'], 'failed')
        self.assertIn('comando 2', report['failure'])

    def test_continue_policy_records_and_goes_on(self):
        connection = FakeConnection(failures={'VALUES (2)': ('duplicate key', '23505')})
        output = self.output(connection, mode=APPLY, on_error=CONTINUE)
        output.open_phase('load_transformed_data')
        for number in (1, 2, 3):
            output.write(Insert('students', {'id': number}))
        output.finish(True)

        self.assertEqual(connection.committed, [
            'INSERT INTO students (id) VALUES (1)', 'INSERT INTO students (id) VALUES (3)',
        ])
        report = self.relatorio(output)
        self.assertEqual((report['result'], report['errors']), ('committed', 1))

    def test_close_without_finish_rolls_back(self):
        connection = FakeConnection()
        output = self.output(connection)
        output.open_phase('prepare_target')
        output.write(Insert('students', {'id': 1}))
        output.close()

        self.assertEqual(connection.log[-1], '<rollback>')
        self.assertEqual(connection.committed, [])
        self.assertIn('sem finish(True)', self.relatorio(output)['failure'])

    def test_commit_failure_raises(self):
        connection = FakeConnection(commit_error='violates foreign key constraint')
        output = self.output(connection)
        output.open_phase('prepare_target')
        output.write(Insert('students', {'id': 1}))

        with self.assertRaises(CommitFailedError):
            output.finish(True)

        self.assertEqual(self.relatorio(output)['result'], 'failed')

    def test_failure_before_the_session_opens(self):
        connection = FakeConnection()
        output = self.output(connection)
        output.open_phase('setup')
        output.finish(False)
        output.close()

        self.assertEqual(self.opener.calls, 0)
        self.assertIsNone(output.report)

    def test_describe_and_report(self):
        connection = FakeConnection()
        output = self.output(connection, mode=DRY_RUN, on_error=STOP)
        self.assertEqual(output.describe(), {
            'type': 'direct', 'adapter': 'postgres', 'mode': DRY_RUN, 'on_error': STOP, 'report': None,
        })

        output.open_phase('prepare_target')
        output.finish(True)

        description = output.describe()
        self.assertTrue(description['report'].startswith(os.path.join('aplicacoes', '')))
        self.assertTrue(description['report'].endswith('-dry-run'))
        report = self.relatorio(output)
        self.assertEqual(report['source'], 'generation')
        self.assertNotIn('segredo', json.dumps(report))
        self.assertIn('Execução direta no destino. Modo: dry-run, política de erro: stop', self.messages)

    def test_mirror_finds_the_direct_output(self):
        direct = self.output(FakeConnection())
        file_output = SimpleNamespace()

        self.assertIs(find_direct_output(MirrorOutput(file_output, direct)), direct)
        self.assertIs(find_direct_output(direct), direct)
        self.assertIsNone(find_direct_output(file_output))


class CapabilitySession:
    """Session that knows less than its adapter declares, or defers commands in batches."""

    def __init__(self, capabilities, fail_on=None, describe=None):
        self.capabilities = capabilities
        self.fail_on = fail_on
        self._describe = describe or {'adapter': 'fake'}
        self.batch = []
        self.executed = []
        self.log = []

    @property
    def pending(self):
        return len(self.batch)

    def describe(self):
        return dict(self._describe)

    def commands(self, lines, dry_run, first_line=1, source=None):
        for number, line in enumerate(lines, first_line):
            if line.strip():
                yield Command(line.strip(), number)

    def begin(self):
        self.log.append('begin')

    def execute(self, command):
        self.batch.append(command)

    def flush(self):
        batch, self.batch = self.batch, []
        for position, command in enumerate(batch):
            if self.fail_on and self.fail_on in command.text:
                raise DeferredCommandError(position, RuntimeError('E11000 duplicate key'))
            self.executed.append(command.text)

    def end_artifact(self):
        self.flush()

    def commit(self):
        self.log.append('commit')

    def rollback(self):
        self.log.append('rollback')

    def error_code(self, exc):
        return 11000

    def close(self):
        self.log.append('close')


class FakeTarget:
    name = 'fake'
    artifact_format = SimpleNamespace(name='lines', extension='jsonl')

    def __init__(self, capabilities):
        self.capabilities = capabilities

    def session(self, waits_between_commands=False):
        raise AssertionError('the output opens sessions through its opener')

    def render(self, operation):
        return [f'{operation.entity} {operation.values["id"]}\n']


NO_SAVEPOINTS = Capabilities(transactions=True, savepoints=False, suspend_constraints=False)


class CapabilityTest(DirectOutputTestCase):
    def test_target_without_transactions_is_refused_before_connecting(self):
        opener = RecordingOpener(FakeConnection())
        target = FakeTarget(Capabilities(transactions=False, savepoints=False, suspend_constraints=False))

        for mode in (APPLY, DRY_RUN):
            with self.subTest(mode=mode), self.assertRaises(CapabilityError):
                self.output(None, mode=mode, target=target, opener=opener)
        self.assertEqual(opener.calls, 0)

    def test_dry_run_without_savepoints_defaults_to_stop(self):
        output = self.output(None, mode=DRY_RUN, target=FakeTarget(NO_SAVEPOINTS), opener=lambda: None)
        self.assertEqual(output.on_error, STOP)
        with self.assertRaises(CapabilityError):
            self.output(None, mode=DRY_RUN, on_error=CONTINUE, target=FakeTarget(NO_SAVEPOINTS), opener=lambda: None)

    def test_session_that_knows_less_fails_before_the_first_command(self):
        session = CapabilitySession(Capabilities(transactions=False, savepoints=False, suspend_constraints=False))
        output = self.output(None, target=FakeTarget(NO_SAVEPOINTS), opener=lambda: session)

        with self.assertRaises(CapabilityError):
            output.open_phase('prepare_target')
        output.finish(False)

        self.assertNotIn('begin', session.log)
        self.assertIn('transações', self.relatorio(output)['failure'])

    def test_deferred_failure_names_the_command_on_phase_change(self):
        session = CapabilitySession(NO_SAVEPOINTS, fail_on='students 2')
        output = self.output(None, target=FakeTarget(NO_SAVEPOINTS), opener=lambda: session)
        output.open_phase('load_transformed_data')
        for number in (1, 2, 3):
            output.write(Insert('students', {'id': number}))

        with self.assertRaises(StatementFailedError) as raised:
            output.open_phase('finalize_target')
        output.finish(False)

        self.assertIn('03_load_transformed_data.jsonl, comando 2, linha 2', str(raised.exception))
        self.assertEqual(session.executed, ['students 1'])
        self.assertEqual(session.log[-1], 'rollback')

    def test_transaction_limit_is_shown(self):
        session = CapabilitySession(NO_SAVEPOINTS, describe={'adapter': 'mongodb', 'transaction_limit_seconds': 60})
        output = self.output(None, target=FakeTarget(NO_SAVEPOINTS), opener=lambda: session)
        output.open_phase('prepare_target')
        output.finish(True)

        self.assertTrue(any('MongoDB: 60 s' in message for message in self.messages))
        self.assertEqual(self.relatorio(output)['target']['transaction_limit_seconds'], 60)


class EquivalenceTest(unittest.TestCase):
    """The direct output runs the same commands as the applier over the files."""

    def test_hybrid_dry_run_matches_the_applier(self):
        phases = [
            ('setup', [Comment('ABERTURA')]),
            ('prepare_target', [Clear(('students',)), Native('postgres', 'BEGIN;\nSELECT 1;\nCOMMIT;')]),
            ('copy_compatible_entities', [Insert('holidays', {'id': 1, 'name': "Dia d'Água"})]),
            ('load_transformed_data', [
                Native('postgres', 'SET standard_conforming_strings = off;'),
                Native('postgres', "INSERT INTO notes VALUES ('it\\'s');"),
                Insert('students', {'id': 1, 'name': 'Ana'}),
                Insert('students', {'id': 2, 'name': 'Bia'}),
            ]),
            ('finalize_target', [ResetIdentity('students', 'id')]),
        ]
        failures = {'VALUES (2': ('duplicate key', '23505')}
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = ArtifactManager(os.path.join(tmpdir, 'Acme'), timestamp='20261126-100000')
            direct_connection = FakeConnection(failures=dict(failures))
            direct = DirectDatabaseOutput(
                TARGET, lambda: PostgresSession(direct_connection), artifacts.run_dir, DRY_RUN,
            )
            output = MirrorOutput(FileOutput(artifacts, TARGET), direct)
            for phase, operations in phases:
                output.open_phase(phase)
                for operation in operations:
                    output.write(operation)
            output.finish(True)
            output.close()
            artifacts.write_manifest(status='success', connections={'target': {'adapter': 'postgres'}})

            applier_connection = FakeConnection(failures=dict(failures))
            applied = ArtifactApplier(artifacts.run_dir, lambda: PostgresSession(applier_connection), mode=DRY_RUN).apply()

            self.assertEqual(direct_connection.log, applier_connection.log)
            direct_files = direct.report.as_dict()['files']
            applied_files = applied.as_dict()['files']
            for direct_file, applied_file in zip(direct_files, applied_files, strict=True):
                for key in ('file', 'executed', 'errors', 'skipped'):
                    self.assertEqual(direct_file[key], applied_file[key], key)
            self.assertEqual(direct.report.error_count, 1)
            self.assertEqual(applied.source, 'artifacts')


class SessionLinesTest(unittest.TestCase):
    def begun(self, connection=None, waits=False):
        session = PostgresSession(connection or FakeConnection(), waits_between_commands=waits)
        session.begin()
        return session

    def test_file_and_rendered_text_give_the_same_commands(self):
        text = "-- comentário\nINSERT INTO a VALUES (1);\n\nINSERT INTO a\nVALUES (2);\n"
        session = self.begun()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'x.sql')
            with open(path, 'w', encoding='utf-8') as sql_file:
                sql_file.write(text)
            with open(path, encoding='utf-8') as lines:
                from_file = list(session.commands(lines, dry_run=False))
        from_text = list(session.commands(text.splitlines(keepends=True), dry_run=False))
        shifted = list(session.commands(text.splitlines(keepends=True), dry_run=False, first_line=120))

        self.assertEqual(from_file, from_text)
        self.assertEqual([command.line for command in from_file], [2, 4])
        self.assertEqual([command.line for command in shifted], [121, 123])

    def test_setting_carries_over_between_calls_until_end_artifact(self):
        session = self.begun()
        text = ["INSERT INTO notes VALUES ('it\\'s');\n"]

        list(session.commands(['SET standard_conforming_strings = off;\n'], dry_run=False))
        commands = list(session.commands(text, dry_run=False))
        self.assertEqual([command.text for command in commands], ["INSERT INTO notes VALUES ('it\\'s')"])

        session.end_artifact()
        with self.assertRaises(ArtifactReadError):
            list(session.commands(text, dry_run=False, source=LOAD))

    def test_waits_between_commands_turns_off_the_idle_limit(self):
        waiting = FakeConnection()
        session = self.begun(waiting, waits=True)
        session.end_artifact()

        self.assertEqual(
            [sql for sql in waiting.log if 'idle' in sql or sql == 'RESET ALL'],
            ['SET idle_in_transaction_session_timeout = 0', 'RESET ALL', 'SET idle_in_transaction_session_timeout = 0'],
        )

        plain = FakeConnection()
        self.begun(plain).end_artifact()
        self.assertFalse(any('idle' in sql for sql in plain.log))

    def test_adapter_session_passes_the_option(self):
        adapter = PostgresTargetAdapter(FakeConnection())

        self.assertTrue(adapter.session(waits_between_commands=True).waits_between_commands)
        self.assertFalse(adapter.session().waits_between_commands)


ENV_LINES = [
    'SOURCE_V1_ADAPTER=postgres', 'SOURCE_V1_HOST=source-db', 'SOURCE_V1_NAME=source', 'SOURCE_V1_USER=u',
    'SOURCE_V1_PASS=p',
    'TARGET_ADAPTER=postgres', 'TARGET_HOST=target-db', 'TARGET_PORT=5432', 'TARGET_NAME=target',
    'TARGET_USER=target-user', 'TARGET_PASS=segredo',
]


class OutputSelectionTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.clientdir = os.path.join(self._tmpdir.name, 'Acme')
        os.makedirs(self.clientdir)
        with open(os.path.join(self.clientdir, '.env'), 'w', encoding='utf-8') as env_file:
            env_file.write('\n'.join(ENV_LINES))

    def tearDown(self):
        self._tmpdir.cleanup()

    def build(self, settings=None):
        def connect(config, role):
            return SimpleNamespace(close=lambda: None) if role == 'source' else PostgresTargetAdapter()

        openers = []

        def opener(adapter_class, config, waits_between_commands=False):
            openers.append((adapter_class.__name__, config.get('host'), waits_between_commands))
            return lambda: None

        with patch('core.context.connect_adapter', side_effect=connect), \
             patch('core.context.session_opener', side_effect=opener), redirect_stdout(io.StringIO()):
            if settings is None:
                context = DefaultMigrationContextFactory.from_clientdir(self.clientdir)
            else:
                with output_settings(settings):
                    context = DefaultMigrationContextFactory.from_clientdir(self.clientdir)
        self.addCleanup(context.close)
        return context, openers

    def test_default_is_the_file_output(self):
        context, openers = self.build()

        self.assertIsInstance(context.output, FileOutput)
        self.assertEqual(openers, [])

    def test_direct_output_does_not_connect_while_building(self):
        context, openers = self.build(OutputSettings(DIRECT_OUTPUT, DRY_RUN))

        self.assertIsInstance(context.output, DirectDatabaseOutput)
        self.assertEqual(context.output.mode, DRY_RUN)
        self.assertEqual(openers, [('PostgresTargetAdapter', 'target-db', True)])
        self.assertIsNone(context.output.report)
        context.write_manifest('running')
        with open(context.artifacts.manifest_path, encoding='utf-8') as manifest_file:
            self.assertEqual(json.load(manifest_file)['output']['type'], 'direct')

    def test_hybrid_puts_the_file_first(self):
        context, _ = self.build(OutputSettings(HYBRID_OUTPUT, APPLY, STOP))

        self.assertIsInstance(context.output, MirrorOutput)
        file_output, direct = context.output.outputs
        self.assertIsInstance(file_output, FileOutput)
        self.assertIsInstance(direct, DirectDatabaseOutput)
        self.assertEqual((direct.mode, direct.on_error), (APPLY, STOP))

    def test_settings_need_a_mode_to_write_on_the_target(self):
        with self.assertRaises(ValueError):
            OutputSettings(DIRECT_OUTPUT)
        with self.assertRaises(ValueError):
            OutputSettings('banco')
        self.assertEqual(OutputSettings().output, FILE_OUTPUT)

    def test_settings_hold_only_inside_the_block(self):
        with output_settings(OutputSettings(DIRECT_OUTPUT, APPLY)):
            self.assertEqual(_OUTPUT_SETTINGS.get().output, DIRECT_OUTPUT)
        self.assertEqual(_OUTPUT_SETTINGS.get().output, FILE_OUTPUT)


class MigracaoOutputTest(unittest.TestCase):
    def test_output_choices(self):
        self.assertEqual(Migracao.parse_args(['Acme']).output, FILE_OUTPUT)
        args = Migracao.parse_args(['Acme', '--output', 'hybrid', '--dry-run', '--on-error', 'continue'])
        self.assertEqual((args.output, args.application, args.on_error), (HYBRID_OUTPUT, DRY_RUN, CONTINUE))

    def test_direct_output_needs_a_mode(self):
        for output in (DIRECT_OUTPUT, HYBRID_OUTPUT):
            with self.subTest(output=output), patch('sys.stderr'), self.assertRaises(SystemExit) as raised:
                Migracao.parse_args(['Acme', '--output', output])
            self.assertEqual(raised.exception.code, 2)

    def test_main_builds_the_client_with_the_settings_and_reports_the_run(self):
        seen = {}
        report = SimpleNamespace(result='rolled_back', failure=None, error_count=0, directory='aplicacoes/x')
        direct = SimpleNamespace(report=report)
        closed = []

        def load(client):
            seen['settings'] = _OUTPUT_SETTINGS.get()
            return SimpleNamespace(
                enable_debug=lambda limit: None,
                run=lambda: None,
                context=SimpleNamespace(output=direct, close=lambda: closed.append(True)),
            )

        printed = []
        with patch.object(Migracao, 'load_migration', side_effect=load), \
             patch.object(Migracao, 'find_direct_output', side_effect=lambda output: output), \
             patch('builtins.print', side_effect=printed.append):
            code = Migracao.main(['Acme', '--output', 'direct', '--dry-run'])

        self.assertEqual(code, 0)
        self.assertEqual(seen['settings'], OutputSettings(DIRECT_OUTPUT, DRY_RUN, None))
        self.assertEqual(closed, [True])
        self.assertIn('Resultado: rolled_back. ROLLBACK executado, destino sem alterações.', printed)

    def test_main_without_a_report_exits_with_error(self):
        def load(client):
            return SimpleNamespace(
                enable_debug=lambda limit: None, run=lambda: None,
                context=SimpleNamespace(output=SimpleNamespace(report=None), close=lambda: None),
            )

        printed = []
        with patch.object(Migracao, 'load_migration', side_effect=load), \
             patch.object(Migracao, 'find_direct_output', side_effect=lambda output: output), \
             patch('builtins.print', side_effect=printed.append):
            code = Migracao.main(['Acme', '--output', 'direct', '--apply'])

        self.assertEqual(code, 1)
        self.assertTrue(printed[0].startswith('[ERRO] A execução não chegou ao destino'))


if __name__ == '__main__':
    unittest.main()
