import json
import os
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
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
from core.adapters.ownership import claim
from core.adapters.postgres import PostgresTargetAdapter
from core.adapters.postgres_session import PostgresSession
from core.artifacts import ArtifactManager
from core.base_migration import BaseMigration
from core.compatible import CopyEntity
from core.context import _PARALLEL_SETTINGS, MigrationContext
from core.metrics import GenerationMetrics
from core.outputs import FileOutput, MirrorOutput
from core.outputs.direct import DirectDatabaseOutput
from core.parallel import PROCESS, THREAD, ParallelSettings, ParallelSettingsError
from core.queries import SourceQuery
from core.runtime import MigrationLogger, MigrationProgress
from core.strategies import Copy, Transform
from core.table_migration import TableMigration
from core.tasks import TaskDeclarationError, declared_tasks, task
from tests.apply_fakes import FakeConnection
from tests.source_fakes import FakeSqlSource

TARGET = PostgresTargetAdapter()
TABLES = {
    'tb_curso': [{'id': number, 'name': f'Curso {number}'} for number in range(1, 6)],
    'tb_aluno': [{'id': number, 'name': f"Aluno {number} d'Ávila", 'notes': 'linha 1\r\nlinha 2'} for number in range(1, 301)],
    'tb_professor': [{'id': number, 'name': f'Prof {number}'} for number in range(1, 21)],
    'tb_matricula': [{'id': number, 'student': number % 300 + 1} for number in range(1, 401)],
    'feriados': [{'id': 1, 'name': 'Natal'}, {'id': 2, 'name': 'Ano Novo'}],
    'tb_log': [{'id': 1}],
}
ON_LINUX = sys.platform.startswith('linux')


class FakeTables(FakeSqlSource):
    """SQL source that returns the rows of the table named in the query, owned by its creator."""

    def __init__(self, delays=None):
        super().__init__()
        self.delays = delays or {}
        claim(self)

    def _table(self, sql):
        return re.search(r'FROM (\w+)', sql).group(1)

    def _rows(self, sql):
        table = self._table(sql)
        time.sleep(self.delays.get(table, 0))
        return iter([dict(row) for row in TABLES[table]])

    def _scalar(self, sql):
        return len(TABLES[self._table(sql)])


def build_context(tmpdir, workers=1, mode=THREAD, output=None, delays=None):
    clientdir = os.path.join(tmpdir, 'Acme')
    os.makedirs(clientdir, exist_ok=True)
    artifacts = ArtifactManager(clientdir, timestamp=f'{workers}-{mode}-{time.monotonic_ns()}')
    logger = MigrationLogger(artifacts)
    progress = MigrationProgress(logger)
    logger.progress = progress
    metrics = GenerationMetrics(logger.run_id, events=logger.event)
    logger.metrics = metrics
    parallel = ParallelSettings(workers, mode)
    metrics.parallel = parallel.describe()
    context = MigrationContext(
        clientdir=clientdir, config={}, artifacts=artifacts, sources={'v1': FakeTables(delays)}, target=TARGET,
        output=output(artifacts) if output else FileOutput(artifacts, TARGET), logger=logger, progress=progress,
        metrics=metrics, source_configs={'v1': SimpleNamespace(adapter='postgres', name='v1')}, parallel=parallel,
    )
    context.open_phase('setup')
    return context


def table(source, target, fields):
    return TableMigration(SourceQuery(source, order_by='id'), target, fields)


class Escola(BaseMigration):
    events = []

    def compatible_entities(self):
        return [CopyEntity('feriados', source='v1', target='holidays', order_by='id')]

    @task('courses')
    def insert_courses(self):
        self.comment('COURSES')
        table('tb_curso', 'courses', [Copy('id'), Copy('name')]).run(self.context.source('v1'))

    @task('students')
    def insert_students(self):
        self.print_log('Inserindo alunos')
        self.comment('STUDENTS')
        table('tb_aluno', 'students', [Copy('id'), Copy('name'), Copy('notes')]).run(self.context.source('v1'))

    @task('teachers')
    def insert_teachers(self):
        self.comment('TEACHERS')
        table('tb_professor', 'teachers', [Copy('id'), Copy('name')]).run(self.context.source('v1'))

    @task('enrollments', sequence=2)
    def insert_enrollments(self):
        self.comment('ENROLLMENTS')
        table('tb_matricula', 'enrollments', [Copy('id'), Copy('student')]).run(self.context.source('v1'))


class GenerationRun:
    """Runs a migration class with a fake source connection per worker."""

    def __init__(self, test, migration_class=Escola, **options):
        self.tmpdir = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmpdir.cleanup)
        delays = options.pop('delays', None)
        self.context = build_context(self.tmpdir.name, delays=delays, **options)
        self.migration = migration_class(self.context.clientdir, context=self.context)
        with patch('core.parallel.connect_adapter', side_effect=lambda config, role: FakeTables(delays)), \
                redirect_stdout(StringIO()):
            self.migration.run()
        self.context.close()
        self.artifacts = self.context.artifacts

    def phase(self, phase):
        with open(self.artifacts.command_path(phase, 'sql'), 'rb') as artifact:
            return artifact.read()

    def manifest(self):
        with open(self.artifacts.manifest_path, encoding='utf-8') as manifest_file:
            return json.load(manifest_file)

    def report(self):
        with open(os.path.join(self.artifacts.run_dir, 'relatorio-geracao.json'), encoding='utf-8') as report_file:
            return json.load(report_file)

    def log(self):
        with open(self.artifacts.log_path('migration.log'), encoding='utf-8') as log_file:
            return log_file.read()


class TaskDeclarationTest(unittest.TestCase):
    def test_name_defaults_to_the_method_and_order_follows_sequence(self):
        class Plano(BaseMigration):
            @task(sequence=2)
            def depois(self): pass

            @task
            def primeiro(self): pass

            @task('segundo')
            def outro(self): pass

        self.assertEqual([(spec.name, spec.sequence, spec.method) for spec in declared_tasks(Plano)], [
            ('primeiro', 1, 'primeiro'), ('segundo', 1, 'outro'), ('depois', 2, 'depois'),
        ])

    def test_repeated_name_is_refused_when_the_class_is_defined(self):
        with self.assertRaises(TaskDeclarationError) as raised:
            class Repetida(BaseMigration):
                @task('students')
                def a(self): pass

                @task('students')
                def b(self): pass

        self.assertIn('a()', str(raised.exception))
        self.assertIn('b()', str(raised.exception))

    def test_invalid_declarations(self):
        for options in ({'sequence': 0}, {'sequence': '1'}, {'name': ''}):
            with self.subTest(options=options), self.assertRaises(TaskDeclarationError):
                task(**options)

    def test_override_without_decorator_removes_the_task(self):
        class Filha(Escola):
            def insert_teachers(self):
                pass

        self.assertNotIn('teachers', [spec.name for spec in declared_tasks(Filha)])

    def test_client_without_tasks_keeps_its_run_inserts(self):
        calls = []

        class Antiga(BaseMigration):
            def run_inserts(self):
                calls.append('run_inserts')

        run = GenerationRun(self, Antiga)
        self.assertEqual(calls, ['run_inserts'])
        self.assertEqual(run.manifest()['status'], 'success')


class SameArtifactsTest(unittest.TestCase):
    def assert_same_as_sequential(self, **options):
        sequential = GenerationRun(self)
        parallel = GenerationRun(self, **options)

        self.assertEqual(parallel.manifest()['status'], 'success', parallel.manifest().get('error'))
        for phase in ('copy_compatible_entities', 'load_transformed_data'):
            self.assertEqual(parallel.phase(phase), sequential.phase(phase), phase)
        self.assertFalse(os.path.exists(os.path.join(parallel.artifacts.run_dir, 'tarefas')))
        units = lambda run: [(unit['label'], unit['rows_read'], unit['inserts']) for unit in run.report()['units']]
        self.assertEqual(units(parallel), units(sequential))
        return sequential, parallel

    def test_threads(self):
        _, parallel = self.assert_same_as_sequential(workers=4, mode=THREAD)

        report = parallel.report()
        self.assertEqual(report['parallel'], {'workers': 4, 'mode': THREAD})
        self.assertEqual(parallel.manifest()['parallel'], {'workers': 4, 'mode': THREAD})
        load = [task for task in report['tasks'] if task['phase'] == 'load_transformed_data']
        self.assertEqual([task['name'] for task in load], ['courses', 'students', 'teachers', 'enrollments'])
        self.assertTrue(all(task['worker'].startswith('tarefa') for task in load))
        phase = next(phase for phase in report['phases'] if phase['phase'] == 'load_transformed_data')
        self.assertEqual(phase['tasks'], 4)
        self.assertIsNotNone(phase['tasks_duration_seconds'])
        self.assertEqual(parallel.log().count('Inserindo alunos'), 1)

    @unittest.skipUnless(ON_LINUX, 'o modo processo usa fork')
    def test_processes(self):
        _, parallel = self.assert_same_as_sequential(workers=4, mode=PROCESS)

        self.assertEqual(parallel.log().count('Inserindo alunos'), 1)
        self.assertIn('Tarefa enrollments concluída', parallel.log())

    def test_merge_order_does_not_follow_completion(self):
        run = GenerationRun(self, workers=3, delays={'tb_aluno': 0.3})

        load = run.phase('load_transformed_data').decode('utf-8')
        self.assertLess(load.index('-----STUDENTS'), load.index('-----TEACHERS'))
        finished = {task['name']: task['finished_at'] for task in run.report()['tasks']}
        self.assertLess(finished['teachers'], finished['students'])

    def test_sequential_report_names_the_main_worker(self):
        run = GenerationRun(self)

        self.assertEqual({task['worker'] for task in run.report()['tasks']}, {'principal'})


class SequenceTest(unittest.TestCase):
    def test_higher_sequence_waits_for_lower(self):
        events = []

        class Ordem(BaseMigration):
            @task('a')
            def a(self):
                events.append(('start', 'a'))
                time.sleep(0.2)
                events.append(('end', 'a'))

            @task('b')
            def b(self):
                events.append(('start', 'b'))
                events.append(('end', 'b'))

            @task('c', sequence=2)
            def c(self):
                events.append(('start', 'c'))

        GenerationRun(self, Ordem, workers=3)

        self.assertGreater(events.index(('start', 'c')), events.index(('end', 'a')))
        self.assertGreater(events.index(('start', 'c')), events.index(('end', 'b')))


class FailureTest(unittest.TestCase):
    def test_failing_task_fails_the_run_with_its_name(self):
        class Quebra(Escola):
            @task('teachers')
            def insert_teachers(self):
                raise RuntimeError('origem caiu')

        for workers in (1, 3):
            with self.subTest(workers=workers):
                run = GenerationRun(self, Quebra, workers=workers)
                manifest = run.manifest()
                self.assertEqual(manifest['status'], 'failed')
                self.assertIn('origem caiu', manifest['error'])
                if workers > 1:
                    self.assertIn("'teachers'", manifest['error'])

    def test_failure_cancels_a_running_task(self):
        class Lenta(BaseMigration):
            @task('lenta')
            def lenta(self):
                slow = Transform('name', lambda value, row: time.sleep(0.01) or value)
                table('tb_aluno', 'students', [Copy('id'), slow]).run(self.context.source('v1'))

            @task('quebra')
            def quebra(self):
                time.sleep(0.1)
                raise RuntimeError('origem caiu')

        started = time.monotonic()
        run = GenerationRun(self, Lenta, workers=2)

        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(run.manifest()['status'], 'failed')
        self.assertIn("'quebra'", run.manifest()['error'])

    def test_forbidden_operation_in_a_task(self):
        class Limpa(BaseMigration):
            @task('limpa')
            def limpa(self):
                self.clear(['students'])

        for workers in (1, 2):
            with self.subTest(workers=workers):
                manifest = GenerationRun(self, Limpa, workers=workers).manifest()
                self.assertEqual(manifest['status'], 'failed')
                self.assertIn('Clear', manifest['error'])
                self.assertIn('preparação', manifest['error'])

    def test_engine_created_outside_the_task(self):
        class Guardada(BaseMigration):
            @task('a')
            def a(self):
                list(self.engine.read(SourceQuery('tb_curso')))

            @task('b')
            def b(self):
                pass

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        context = build_context(tmpdir.name, workers=2)
        migration = Guardada(context.clientdir, context=context)
        migration.engine = context.source('v1')
        with patch('core.parallel.connect_adapter', side_effect=lambda config, role: FakeTables()), \
                redirect_stdout(StringIO()):
            migration.run()
        context.close()
        with open(context.artifacts.manifest_path, encoding='utf-8') as manifest_file:
            manifest = json.load(manifest_file)

        self.assertEqual(manifest['status'], 'failed')
        self.assertIn('pertence a outro worker', manifest['error'])

    def test_interrupted_run_is_recorded_as_failed(self):
        class Interrompe(BaseMigration):
            @task('a')
            def a(self):
                raise KeyboardInterrupt

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        context = build_context(tmpdir.name)
        migration = Interrompe(context.clientdir, context=context)
        with redirect_stdout(StringIO()), self.assertRaises(KeyboardInterrupt):
            migration.run()
        context.close()
        with open(context.artifacts.manifest_path, encoding='utf-8') as manifest_file:
            self.assertEqual(json.load(manifest_file)['status'], 'failed')


class SettingsTest(unittest.TestCase):
    def test_process_mode_refused_with_mongodb(self):
        with patch('core.parallel.sys.platform', 'linux'), self.assertRaises(ParallelSettingsError) as raised:
            ParallelSettings(4, PROCESS).check(['postgres', 'mongodb'])
        self.assertIn('mongodb', str(raised.exception))
        ParallelSettings(4, THREAD).check(['mongodb'])
        ParallelSettings(1, PROCESS).check(['mongodb'])

    def test_process_mode_outside_linux(self):
        with patch('core.parallel.sys.platform', 'darwin'), self.assertRaises(ParallelSettingsError) as raised:
            ParallelSettings(2, PROCESS).check(['postgres'])
        self.assertIn('--worker-mode thread', str(raised.exception))

    def test_invalid_settings(self):
        for options in ({'workers': 0}, {'mode': 'fibra'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                ParallelSettings(**options)

    def test_command_line(self):
        args = Migracao.parse_args(['Acme', '--workers', '4', '--worker-mode', 'process'])
        self.assertEqual((args.workers, args.worker_mode), (4, PROCESS))
        self.assertEqual((Migracao.parse_args(['Acme']).workers, Migracao.parse_args(['Acme']).worker_mode), (1, THREAD))
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            Migracao.parse_args(['Acme', '--workers', '0'])

    def test_main_passes_the_settings_to_the_factory(self):
        seen = {}

        def load(client):
            seen['settings'] = _PARALLEL_SETTINGS.get()
            raise Migracao.ClientNotFoundError('parar aqui')

        with patch.object(Migracao, 'load_migration', side_effect=load), patch('builtins.print'):
            Migracao.main(['Acme', '--workers', '3'])

        self.assertEqual(seen['settings'], ParallelSettings(3, THREAD))


class HybridOutputTest(unittest.TestCase):
    def test_hybrid_runs_the_same_commands_with_workers(self):
        def hybrid(connection):
            return lambda artifacts: MirrorOutput(
                FileOutput(artifacts, TARGET),
                DirectDatabaseOutput(TARGET, lambda: PostgresSession(connection), artifacts.run_dir, 'dry-run'),
            )

        sequential, parallel = FakeConnection(), FakeConnection()
        GenerationRun(self, output=hybrid(sequential))
        run = GenerationRun(self, workers=4, output=hybrid(parallel))

        self.assertEqual(run.manifest()['status'], 'success', run.manifest().get('error'))
        self.assertEqual(parallel.log, sequential.log)
        self.assertTrue(any('linha 1\r\nlinha 2' in sql for sql in parallel.log))


    def test_target_failure_stops_the_generation(self):
        connection = FakeConnection(failures={'Curso 3': ('duplicate key', '23505')})

        def direct(artifacts):
            return DirectDatabaseOutput(TARGET, lambda: PostgresSession(connection), artifacts.run_dir, 'apply')

        run = GenerationRun(self, workers=3, output=direct)

        manifest = run.manifest()
        self.assertEqual(manifest['status'], 'failed')
        self.assertIn("'courses'", manifest['error'])
        self.assertEqual(connection.committed, [])

if __name__ == '__main__':
    unittest.main()
