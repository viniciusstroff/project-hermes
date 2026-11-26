import json
import os
import re
import sys
import tempfile
import time
import types
import unittest
from contextlib import nullcontext, redirect_stdout
from io import StringIO
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
from core.artifacts import ArtifactManager
from core.base_migration import BaseMigration
from core.checkpoint import CHECKPOINT_FILE, Checkpoint, CheckpointError, ResumeSettings, resume_settings
from core.compatible import CopyEntity
from core.context import parallel_settings
from core.parallel import ParallelSettings
from core.queries import SourceQuery
from core.strategies import Copy, Transform
from core.table_migration import TableMigration
from core.tasks import task
from tests.source_fakes import FakeSqlSource

TABLES = {
    'tb_curso': [{'id': number, 'name': f'Curso {number}'} for number in range(1, 6)],
    'tb_aluno': [{'id': number, 'name': f'Aluno {number}', 'notes': 'a\r\nb'} for number in range(1, 201)],
    'tb_professor': [{'id': number, 'name': f'Prof {number}'} for number in range(1, 31)],
    'tb_matricula': [{'id': number, 'student': number % 200 + 1} for number in range(1, 301)],
    'feriados': [{'id': 1, 'name': 'Natal'}, {'id': 2, 'name': 'Ano Novo'}],
}
ENV = '\n'.join([
    'SOURCE_V1_ADAPTER=postgres', 'SOURCE_V1_HOST=source-db', 'SOURCE_V1_NAME=source', 'SOURCE_V1_USER=u',
    'SOURCE_V1_PASS=p', 'TARGET_ADAPTER=postgres', 'TARGET_HOST=target-db', 'TARGET_NAME=target',
    'TARGET_USER=t', 'TARGET_PASS=segredo',
])
PHASE_FILES = ['00_setup.sql', '01_prepare_target.sql', '02_copy_compatible_entities.sql',
               '03_load_transformed_data.sql', '04_finalize_target.sql']


class FakeTables(FakeSqlSource):
    def __init__(self):
        super().__init__()
        claim(self)

    def _table(self, sql):
        return re.search(r'FROM (\w+)', sql).group(1)

    def _rows(self, sql):
        return iter([dict(row) for row in TABLES[self._table(sql)]])

    def _scalar(self, sql):
        return len(TABLES[self._table(sql)])


def table(source, target, fields):
    return TableMigration(SourceQuery(source, order_by='id'), target, fields)


def failing(name, at_id):
    """A field that raises at row ``at_id`` while ``name`` is in Escola.FAIL."""

    def value(raw, row):
        if name in Escola.FAIL and row['id'] == at_id:
            raise RuntimeError(f'origem caiu em {name}')
        return raw

    return Transform('name', value)


class Escola(BaseMigration):
    FAIL = set()
    RAN = []
    INITIAL_COMMENT = 'LIMPEZA'

    def compatible_entities(self):
        return [CopyEntity('feriados', source='v1', target='holidays', order_by='id')]

    def run_initial(self):
        self.comment(Escola.INITIAL_COMMENT)
        self.clear(['courses', 'students', 'teachers', 'enrollments'])

    @task('courses')
    def insert_courses(self):
        Escola.RAN.append('courses')
        self.comment('COURSES')
        table('tb_curso', 'courses', [Copy('id'), Copy('name')]).run(self.context.source('v1'))

    @task('students')
    def insert_students(self):
        Escola.RAN.append('students')
        if 'students-slow' in Escola.FAIL:
            time.sleep(0.4)
        self.comment('STUDENTS')
        table('tb_aluno', 'students', [Copy('id'), failing('students', 150), Copy('notes')]).run(self.context.source('v1'))

    @task('teachers')
    def insert_teachers(self):
        Escola.RAN.append('teachers')
        self.comment('TEACHERS')
        table('tb_professor', 'teachers', [Copy('id'), failing('teachers', 10)]).run(self.context.source('v1'))

    @task('enrollments', sequence=2)
    def insert_enrollments(self):
        Escola.RAN.append('enrollments')
        self.comment('ENROLLMENTS')
        table('tb_matricula', 'enrollments', [Copy('id'), Copy('student')]).run(self.context.source('v1'))

    def reset_sequences(self):
        self.reset_identity(['courses', 'students'], column='id')


def connect(config, role):
    return FakeTables() if role == 'source' else PostgresTargetAdapter()


class CheckpointTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        Escola.FAIL = set()
        Escola.RAN = []
        Escola.INITIAL_COMMENT = 'LIMPEZA'

    def clientdir(self, name='Acme'):
        path = os.path.join(self._tmpdir.name, name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, '.env'), 'w', encoding='utf-8') as env_file:
            env_file.write(ENV)
        return path

    def generate(self, clientdir, resume=None, workers=1, debug=None):
        Escola.RAN = []
        resume_block = resume_settings(resume) if resume else nullcontext()
        with patch('core.context.connect_adapter', side_effect=connect), \
                patch('core.parallel.connect_adapter', side_effect=connect), \
                redirect_stdout(StringIO()), parallel_settings(ParallelSettings(workers)), resume_block:
            migration = Escola(clientdir)
            if debug:
                migration.enable_debug(debug)
            migration.run()
            migration.context.close()
        return migration.context.artifacts

    def phases(self, artifacts):
        return {name: _read_bytes(os.path.join(artifacts.commands_dir, name)) for name in PHASE_FILES}

    def manifest(self, artifacts):
        return _read_json(artifacts.manifest_path)

    def checkpoint(self, artifacts):
        return _read_json(os.path.join(artifacts.run_dir, CHECKPOINT_FILE))

    def report(self, artifacts):
        return _read_json(os.path.join(artifacts.run_dir, 'relatorio-geracao.json'))

    def log(self, artifacts):
        with open(artifacts.log_path('migration.log'), encoding='utf-8') as log_file:
            return log_file.read()

    def clean_phases(self):
        return self.phases(self.generate(self.clientdir('Limpa')))

    def failed_run(self, fail, workers=1):
        Escola.FAIL = set(fail)
        artifacts = self.generate(self.clientdir(), workers=workers)
        self.assertEqual(self.manifest(artifacts)['status'], 'failed')
        Escola.FAIL = set()
        return artifacts


class ResumeTest(CheckpointTestCase):
    def test_finished_tasks_are_skipped_and_artifacts_match_a_clean_run(self):
        clean = self.clean_phases()
        failed = self.failed_run({'teachers'})
        load = os.path.join(failed.commands_dir, '03_load_transformed_data.sql')
        marks = self.checkpoint(failed)['phases']['load_transformed_data']['marks']
        self.assertEqual([mark['name'] for mark in marks], ['courses', 'students'])
        # The failed task left part of its commands after the last confirmed task.
        self.assertGreater(os.path.getsize(load), marks[-1]['end'])

        resumed = self.generate(self.clientdir(), resume=ResumeSettings(failed.timestamp))

        self.assertEqual(resumed.run_dir, failed.run_dir)
        self.assertEqual(self.manifest(resumed)['status'], 'success')
        self.assertEqual(Escola.RAN, ['teachers', 'enrollments'])
        self.assertEqual(self.phases(resumed), clean)
        report = self.report(resumed)
        resumed_tasks = {item['name'] for item in report['tasks'] if item['resumed']}
        self.assertEqual(resumed_tasks, {'copy:holidays', 'courses', 'students'})
        self.assertEqual([attempt['status'] for attempt in report['attempts']], ['failed', 'success'])
        self.assertEqual(self.checkpoint(resumed)['status'], 'success')
        units = {unit['label']: unit['rows_read'] for unit in report['units']}
        self.assertEqual(units['students'], 200)

    def test_parallel_failure_reuses_finished_segments(self):
        clean = self.clean_phases()
        failed = self.failed_run({'students', 'students-slow'}, workers=3)
        checkpoint = self.checkpoint(failed)
        self.assertIn('teachers', {segment['name'] for segment in checkpoint['segments']})

        resumed = self.generate(self.clientdir(), resume=ResumeSettings(failed.timestamp), workers=3)

        self.assertEqual(self.manifest(resumed)['status'], 'success')
        self.assertNotIn('teachers', Escola.RAN)
        self.assertIn('students', Escola.RAN)
        self.assertEqual(self.phases(resumed), clean)
        self.assertFalse(os.path.exists(os.path.join(resumed.run_dir, 'tarefas')))

    def test_rerun_generates_the_task_and_what_follows(self):
        clean = self.clean_phases()
        failed = self.failed_run({'teachers'})

        resumed = self.generate(self.clientdir(), resume=ResumeSettings(failed.timestamp, rerun='courses'))

        self.assertEqual(Escola.RAN, ['courses', 'students', 'teachers', 'enrollments'])
        self.assertEqual(self.phases(resumed), clean)

    def test_unknown_rerun_task_is_refused(self):
        failed = self.failed_run({'teachers'})

        with self.assertRaises(CheckpointError) as raised:
            self.generate(self.clientdir(), resume=ResumeSettings(failed.timestamp, rerun='teachers'))
        self.assertIn("'teachers'", str(raised.exception))

    def test_changed_task_code_is_reported(self):
        failed = self.failed_run({'teachers'})
        path = os.path.join(failed.run_dir, CHECKPOINT_FILE)
        data = _read_json(path)
        data['phases']['load_transformed_data']['marks'][0]['code_hash'] = 'antigo'
        with open(path, 'w', encoding='utf-8') as checkpoint_file:
            json.dump(data, checkpoint_file)

        resumed = self.generate(self.clientdir(), resume=ResumeSettings(failed.timestamp))

        self.assertIn('A tarefa courses mudou desde que foi gerada', self.log(resumed))
        self.assertIn('--rerun courses', self.log(resumed))

    def test_interrupted_run_keeps_its_checkpoint(self):
        class Interrompe(Escola):
            @task('teachers')
            def insert_teachers(self):
                raise KeyboardInterrupt

        clientdir = self.clientdir()
        with patch('core.context.connect_adapter', side_effect=connect), redirect_stdout(StringIO()):
            migration = Interrompe(clientdir)
            with self.assertRaises(KeyboardInterrupt):
                migration.run()
            migration.context.close()
        artifacts = migration.context.artifacts

        self.assertEqual(self.manifest(artifacts)['status'], 'failed')
        checkpoint = self.checkpoint(artifacts)
        self.assertEqual(checkpoint['status'], 'failed')
        self.assertEqual([mark['name'] for mark in checkpoint['phases']['load_transformed_data']['marks']],
                         ['courses', 'students'])


class RefusalTest(CheckpointTestCase):
    def assert_refused_without_changes(self, failed, expected, **options):
        before = self.phases(failed)
        resumed = self.generate(self.clientdir(), resume=ResumeSettings(failed.timestamp), **options)

        manifest = self.manifest(resumed)
        self.assertEqual(manifest['status'], 'failed')
        self.assertIn(expected, manifest['error'])
        self.assertEqual(self.phases(resumed), before)
        return manifest

    def test_finished_run_is_refused(self):
        artifacts = self.generate(self.clientdir())

        with self.assertRaises(CheckpointError) as raised:
            self.generate(self.clientdir(), resume=ResumeSettings(artifacts.timestamp))
        self.assertIn('já terminou', str(raised.exception))

    def test_changed_fingerprint_is_refused(self):
        failed = self.failed_run({'teachers'})

        self.assert_refused_without_changes(failed, 'limite de debug', debug=10)

    def test_refused_resume_keeps_the_checkpoint_usable(self):
        failed = self.failed_run({'teachers'})
        self.assert_refused_without_changes(failed, 'limite de debug', debug=10)

        resumed = self.generate(self.clientdir(), resume=ResumeSettings(failed.timestamp))

        self.assertEqual(self.manifest(resumed)['status'], 'success')
        self.assertEqual(Escola.RAN, ['teachers', 'enrollments'])

    def test_changed_source_is_refused(self):
        failed = self.failed_run({'teachers'})
        grown = [*TABLES['tb_aluno'], {'id': 201, 'name': 'Novo', 'notes': None}]

        with patch.dict(TABLES, {'tb_aluno': grown}):
            manifest = self.assert_refused_without_changes(failed, 'A origem mudou')
        self.assertIn('students/students: 200 -> 201', manifest['error'])

    def test_divergent_script_is_refused(self):
        failed = self.failed_run({'teachers'})
        Escola.INITIAL_COMMENT = 'LIMPEZA DIFERENTE'

        self.assert_refused_without_changes(failed, 'divergiu na fase prepare_target')

    def test_run_in_use_is_refused(self):
        failed = self.failed_run({'teachers'})
        held = Checkpoint.open(failed.run_dir, ResumeSettings(failed.timestamp))
        try:
            with self.assertRaises(CheckpointError) as raised:
                self.generate(self.clientdir(), resume=ResumeSettings(failed.timestamp))
        finally:
            held.release()
        self.assertIn('em uso', str(raised.exception))

    def test_command_line(self):
        args = Migracao.parse_args(['Acme', '--resume', '20261126-101500', '--rerun', 'students'])
        self.assertEqual((args.resume, args.rerun), ('20261126-101500', 'students'))
        for argv in (['Acme', '--rerun', 'students'], ['Acme', '--resume', 'x', '--output', 'direct', '--apply']):
            with self.subTest(argv=argv), patch('sys.stderr'), self.assertRaises(SystemExit):
                Migracao.parse_args(argv)

    def test_new_runs_in_the_same_second_get_their_own_directory(self):
        clientdir = self.clientdir()
        with patch('core.artifacts.datetime') as fake_datetime:
            fake_datetime.datetime.now.return_value.strftime.return_value = '20261126-101500'
            first = ArtifactManager(clientdir)
            second = ArtifactManager(clientdir)

        self.assertEqual((first.timestamp, second.timestamp), ('20261126-101500', '20261126-101500-2'))
        self.assertNotEqual(first.run_dir, second.run_dir)


def _read_bytes(path):
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as artifact:
        return artifact.read()


def _read_json(path):
    with open(path, encoding='utf-8') as json_file:
        return json.load(json_file)


if __name__ == '__main__':
    unittest.main()
