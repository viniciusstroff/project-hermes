import datetime
import json
import os
import re
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
from core.compatible import CopyEntity, copy_compatible_entities
from core.context import MigrationContext
from core.expand_migration import ExpandMigration
from core.metrics import (
    GENERATION_REPORT,
    INTERRUPTED,
    MAX_SAMPLES,
    SAMPLE_LENGTH,
    GenerationMetrics,
    LossGroup,
    LostValue,
)
from core.migration_engine import MigrationEngine
from core.multi_target_migration import MultiTargetMigration, TargetMigration
from core.operations import Insert
from core.outputs import FileOutput
from core.queries import SourceQuery
from core.references import ReferenceMap, build_reference_map
from core.runner import MigrationRunner
from core.runtime import MigrationLogger, MigrationProgress
from core.strategies import Copy, DateConvert, EmailExtract, Fixed, Lookup
from core.table_migration import TableMigration
from tests.source_fakes import FakeSqlSource


class FakeClock:
    """Each call advances one second, so durations are exact."""

    def __init__(self):
        self.value = 0.0

    def __call__(self):
        self.value += 1.0
        return self.value


def fixed_now():
    return datetime.datetime(2026, 11, 26, 10, 0, 0)


class BrokenSource(FakeSqlSource):
    """Source that yields its rows and then loses the connection."""

    def _rows(self, sql):
        self.queries.append(sql)
        yield from self.rows
        raise ConnectionError('conexão com a origem perdida')


class ClientField:
    """Client strategy that follows the contract without inheriting FieldStrategy."""

    def __init__(self, name):
        self.name = name

    @property
    def select_columns(self):
        return [self.name]

    @property
    def insert_col(self):
        return self.name

    def raw_value(self, row):
        return row[self.name]


def statuses_map():
    return build_reference_map(
        ReferenceMap('student_statuses', table='student_statuses', key='code', value='id'),
        [('A', 1), ('I', 2)],
    )


def engine_for(rows, metrics, operations=None, source=None):
    source = source or FakeSqlSource(rows=rows)
    writer = operations.append if operations is not None else (lambda operation: None)
    return MigrationEngine(source, PostgresTargetAdapter(), writer, metrics=metrics, source_name='v1')


class UnitMetricsTest(unittest.TestCase):
    def setUp(self):
        self.metrics = GenerationMetrics('Acme/20261126-100000', clock=FakeClock(), now=fixed_now)
        self.metrics.open_phase('load_transformed_data')

    def test_table_migration_counts_rows_and_inserts(self):
        rows = [{'id': number} for number in range(1500)]
        TableMigration(SourceQuery('tb_aluno'), 'students', [Copy('id')]).run(engine_for(rows, self.metrics))

        unit = self.metrics.units[0]
        self.assertEqual((unit.label, unit.source, unit.phase), ('students', 'v1', 'load_transformed_data'))
        self.assertEqual(unit.rows_read, 1500)
        self.assertEqual(unit.inserts, {'students': 1500})
        self.assertEqual(unit.state, 'done')
        self.assertGreater(unit.duration_seconds, 0)

    def test_expand_migration_counts_source_rows(self):
        rows = [{'id': number} for number in range(10)]
        migration = ExpandMigration(SourceQuery('tb_contato'), 'contacts', [Copy('id')], 'kind', [1, 2, 3])
        migration.run(engine_for(rows, self.metrics))

        unit = self.metrics.units[0]
        self.assertEqual(unit.rows_read, 10)
        self.assertEqual(unit.inserts, {'contacts': 30})

    def test_multi_target_counts_each_target(self):
        rows = [{'id': number} for number in range(100)]
        migration = MultiTargetMigration(SourceQuery('tb_pessoa'), [
            TargetMigration('people', [Copy('id')]),
            TargetMigration('teachers', [Copy('id')], condition=lambda row: row['id'] < 40),
        ])
        migration.run(engine_for(rows, self.metrics))

        unit = self.metrics.units[0]
        self.assertEqual(unit.label, 'people')
        self.assertEqual(unit.rows_read, 100)
        self.assertEqual(unit.inserts, {'people': 100, 'teachers': 40})

    def test_copy_entity_is_a_unit(self):
        rows = [{'id': 1, 'name': 'Natal'}, {'id': 2, 'name': 'Ano Novo'}]
        CopyEntity('feriados', source='v1', target='holidays').run(engine_for(rows, self.metrics))

        unit = self.metrics.units[0]
        self.assertEqual((unit.label, unit.rows_read, unit.inserts), ('holidays', 2, {'holidays': 2}))

    def test_exception_interrupts_the_unit(self):
        rows = [{'id': number} for number in range(250)]
        engine = engine_for(rows, self.metrics, source=BrokenSource(rows=rows))

        with self.assertRaises(ConnectionError):
            TableMigration(SourceQuery('tb_aluno'), 'students', [Copy('id')]).run(engine)

        unit = self.metrics.units[0]
        self.assertEqual(unit.rows_read, 250)
        self.assertEqual(unit.state, INTERRUPTED)
        self.assertIsNone(self.metrics.unit_label)

    def test_insert_outside_a_unit_is_not_counted(self):
        operations = []
        engine = engine_for([{'id': 1}], self.metrics, operations)

        engine.insert('audit', [('id', 1)])
        TableMigration(SourceQuery('tb_aluno'), 'students', [Copy('id')]).run(engine)

        self.assertEqual(len(operations), 2)
        self.assertEqual([unit.inserts for unit in self.metrics.units], [{'students': 1}])

    def test_engine_without_metrics_counts_nothing(self):
        operations = []
        engine = MigrationEngine(FakeSqlSource(rows=[{'id': 1}]), PostgresTargetAdapter(), operations.append)

        TableMigration(SourceQuery('tb_aluno'), 'students', [Copy('id')]).run(engine)

        self.assertEqual(operations, [Insert('students', {'id': 1})])

    def test_phase_totals_and_native_copy(self):
        metrics = GenerationMetrics(clock=FakeClock(), now=fixed_now)
        metrics.open_phase('copy_compatible_entities')
        with metrics.unit('holidays', source='v1') as unit:
            unit.row_read()
            metrics.inserted('holidays')
        metrics.native_copy('v1', 'pg_dump', ['courses'], 1.5, 2048)
        metrics.open_phase('load_transformed_data')

        phase = metrics.phases[0].as_dict()
        self.assertEqual((phase['units'], phase['rows_read'], phase['inserts']), (1, 1, 1))
        self.assertEqual(phase['native_copies'], [
            {'source': 'v1', 'tool': 'pg_dump', 'entities': ['courses'], 'duration_seconds': 1.5, 'bytes': 2048},
        ])
        self.assertIsNotNone(phase['duration_seconds'])

    def test_finish_closes_open_units_as_interrupted(self):
        context_manager = self.metrics.unit('students')
        context_manager.__enter__()

        self.metrics.finish('failed', 'erro')

        self.assertEqual(self.metrics.units[0].state, INTERRUPTED)
        self.assertEqual((self.metrics.status, self.metrics.error), ('failed', 'erro'))
        self.assertIsNone(self.metrics.phase_name)


class LossTest(unittest.TestCase):
    def setUp(self):
        self.metrics = GenerationMetrics(clock=FakeClock(), now=fixed_now)
        self.metrics.open_phase('load_transformed_data')

    def test_lookup_miss_is_recorded_and_fallback_written(self):
        rows = [{'status': value} for value in ('A', 'X', 'Z', 'X', None, '  ', 'I')]
        operations = []
        migration = TableMigration(SourceQuery('tb_aluno'), 'students', [
            Lookup('status', statuses_map(), v2_name='status_id', fallback=0),
        ])
        migration.run(engine_for(rows, self.metrics, operations))

        self.assertEqual([operation.values['status_id'] for operation in operations], [1, 0, 0, 0, 0, 0, 2])
        group = list(self.metrics.units[0].losses.values())[0].as_dict()
        self.assertEqual(group, {
            'entity': 'students', 'column': 'status_id', 'strategy': 'Lookup', 'reason': 'lookup_miss',
            'reference_map': 'student_statuses', 'count': 3, 'samples': ['X', 'Z'],
        })

    def test_samples_are_bounded(self):
        group = LossGroup('students', 'status_id', 'Lookup', 'lookup_miss')
        for number in range(5000):
            group.add(f'chave-{number % 300}')
        group.add('x' * 150)

        self.assertEqual(group.count, 5001)
        self.assertEqual(len(group.samples), MAX_SAMPLES)

        long_sample = LossGroup('students', 'notes', 'EmailExtract', 'email_not_found')
        long_sample.add('x' * 150)
        self.assertEqual(long_sample.samples, ['x' * SAMPLE_LENGTH])

    def test_client_strategy_without_resolve_records_no_loss(self):
        operations = []
        TableMigration(SourceQuery('tb_aluno'), 'students', [ClientField('status')]).run(
            engine_for([{'status': 'X'}], self.metrics, operations),
        )

        self.assertEqual(operations[0].values, {'status': 'X'})
        self.assertEqual(self.metrics.units[0].losses, {})

    def test_summary_lines(self):
        rows = [{'status': 'X'}, {'status': 'Z'}]
        TableMigration(SourceQuery('tb_aluno'), 'students', [
            Lookup('status', statuses_map(), v2_name='status_id'),
        ]).run(engine_for(rows, self.metrics))
        self.metrics.finish('success')

        summary = self.metrics.summary()
        self.assertEqual(summary[0], ('info', '----- Resumo da geração -----'))
        self.assertEqual(summary[1][0], 'info')
        self.assertRegex(summary[1][1], r'^students \(v1\): 2 linhas lidas, 2 inserts, \d+\.\d\d s$')
        self.assertEqual(summary[2], (
            'warning',
            "[AVISO] students.status_id: 2 valores sem correspondência no mapa student_statuses (ex.: 'X', 'Z')",
        ))

    def test_report_content(self):
        rows = [{'status': 'X'}]
        TableMigration(SourceQuery('tb_aluno'), 'students', [Lookup('status', statuses_map())]).run(
            engine_for(rows, self.metrics),
        )
        self.metrics.finish('success')

        report = self.metrics.report(debug_limit=100)

        self.assertEqual(report['status'], 'success')
        self.assertEqual(report['debug'], {'enabled': True, 'limit': 100})
        self.assertEqual(report['totals'], {'units': 1, 'rows_read': 1, 'inserts': 1, 'losses': 1})
        self.assertEqual(report['phases'][0]['phase'], 'load_transformed_data')
        self.assertEqual(report['units'][0]['losses'][0]['samples'], ['X'])
        self.assertEqual(report['started_at'], '2026-11-26T10:00:00.000')


class StrategyResolveTest(unittest.TestCase):
    def test_lookup(self):
        loaded = Lookup('status', statuses_map(), fallback=0)
        plain = Lookup('status', {'A': 1}, fallback=0)

        self.assertEqual(loaded.resolve({'status': 'X'}), LostValue(0, 'lookup_miss', 'X', 'student_statuses'))
        self.assertEqual(plain.resolve({'status': ' X '}), LostValue(0, 'lookup_miss', 'X', None))
        self.assertEqual(plain.raw_value({'status': 'X'}), 0)
        self.assertEqual(plain.resolve({'status': 'A'}), 1)
        self.assertEqual(plain.resolve({'status': None}), 0)
        self.assertEqual(plain.resolve({'status': ''}), 0)

    def test_date_convert(self):
        strategy = DateConvert('birth')

        self.assertEqual(strategy.resolve({'birth': '31/02/2001'}), LostValue(None, 'invalid_date', '31/02/2001'))
        self.assertIsNone(strategy.raw_value({'birth': '31/02/2001'}))
        self.assertEqual(strategy.resolve({'birth': '01/02/2001'}), datetime.date(2001, 2, 1))
        self.assertIsNone(strategy.resolve({'birth': None}))
        self.assertIsNone(strategy.resolve({'birth': '  '}))

    def test_email_extract(self):
        strategy = EmailExtract('notes')

        self.assertEqual(strategy.resolve({'notes': 'ligar à tarde'}), LostValue(None, 'email_not_found', 'ligar à tarde'))
        self.assertEqual(strategy.resolve({'notes': 'contato: ana@escola.edu'}), 'ana@escola.edu')
        self.assertIsNone(strategy.resolve({'notes': None}))
        self.assertIsNone(strategy.resolve({'notes': ''}))

    def test_default_resolve_is_raw_value(self):
        self.assertEqual(Copy('id').resolve({'id': 7}), 7)
        self.assertEqual(Fixed('kind', 3).resolve({}), 3)


class LoggerTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.artifacts = ArtifactManager(os.path.join(self._tmpdir.name, 'Acme'), timestamp='20261126-100000')
        self.logger = MigrationLogger(self.artifacts)
        self.metrics = GenerationMetrics(self.logger.run_id, events=self.logger.event)
        self.logger.metrics = self.metrics

    def tearDown(self):
        self.logger.close()
        self._tmpdir.cleanup()

    def read_logs(self):
        self.logger.close()
        with open(self.artifacts.log_path('migration.log'), encoding='utf-8') as text_file:
            text = text_file.read().splitlines()
        with open(self.artifacts.log_path('migration.jsonl'), encoding='utf-8') as events_file:
            events = [json.loads(line) for line in events_file]
        return text, events

    def test_log_line_becomes_an_event(self):
        self.metrics.open_phase('load_transformed_data')
        with redirect_stdout(StringIO()):
            self.logger.print_log('Inserindo alunos')
            self.logger.print_log('[ERRO] falhou', level='error')

        text, events = self.read_logs()
        self.assertRegex(text[0], r'^\[\d\d/\d\d/\d{4} \d\d:\d\d:\d\d\] Inserindo alunos$')
        self.assertTrue(text[1].endswith('] [ERRO] falhou'))
        logs = [event for event in events if event['event'] == 'log']
        self.assertEqual(logs[0]['message'], 'Inserindo alunos')
        self.assertEqual((logs[0]['level'], logs[0]['phase'], logs[0]['run_id']),
                         ('info', 'load_transformed_data', 'Acme/20261126-100000'))
        self.assertEqual(logs[1]['level'], 'error')
        self.assertEqual(set(logs[0]), {'ts', 'run_id', 'level', 'phase', 'unit', 'event', 'message', 'data'})

    def test_unit_and_loss_events(self):
        self.metrics.open_phase('load_transformed_data')
        TableMigration(SourceQuery('tb_aluno'), 'students', [Lookup('status', statuses_map())]).run(
            engine_for([{'status': 'X'}], self.metrics),
        )

        text, events = self.read_logs()
        names = [event['event'] for event in events]
        self.assertEqual(names, ['phase_started', 'unit_started', 'unit_finished', 'data_loss'])
        finished = events[2]
        self.assertEqual((finished['unit'], finished['phase']), ('students', 'load_transformed_data'))
        self.assertEqual(finished['data']['rows_read'], 1)
        self.assertEqual(finished['data']['inserts'], {'students': 1})
        self.assertEqual(events[3]['level'], 'warning')
        self.assertEqual(events[3]['data']['reason'], 'lookup_miss')
        # Events go to migration.jsonl only.
        self.assertEqual(text, [])

    def test_invalid_level_is_refused(self):
        with self.assertRaises(ValueError):
            self.logger.event('log', 'mensagem', level='debug')


class RecordingNativeCopy:
    name = 'pg_dump'
    target_adapter = 'postgres'

    def copy(self, tables, path):
        with open(path, 'w', encoding='utf-8') as dump:
            dump.write('INSERT INTO public.holidays VALUES (1);\n')


class NativeCopyMetricsTest(unittest.TestCase):
    def test_native_copy_is_recorded_in_the_phase(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = FakeSqlSource()
            source.native_copy = RecordingNativeCopy()
            metrics = GenerationMetrics()
            metrics.open_phase('copy_compatible_entities')
            included = []
            context = types.SimpleNamespace(
                artifacts=types.SimpleNamespace(run_dir=tmpdir),
                debug_limit=None,
                metrics=metrics,
                logger=types.SimpleNamespace(print_log=lambda line, level='info': None),
                output=types.SimpleNamespace(include_file=included.append),
                source=lambda name: MigrationEngine(source, PostgresTargetAdapter(), lambda operation: None,
                                                    metrics=metrics, source_name=name),
            )

            copy_compatible_entities(context, [CopyEntity('public.holidays', source='v1')])

            copies = metrics.phases[0].native_copies
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0]['entities'], ['public.holidays'])
            self.assertEqual(copies[0]['bytes'], len('INSERT INTO public.holidays VALUES (1);\n'))
            self.assertEqual(metrics.units, [])


class RunnerReportTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def run_migration(self, source):
        clientdir = os.path.join(self._tmpdir.name, 'Acme')
        os.makedirs(clientdir, exist_ok=True)
        artifacts = ArtifactManager(clientdir)
        logger = MigrationLogger(artifacts)
        progress = MigrationProgress(logger)
        logger.progress = progress
        metrics = GenerationMetrics(logger.run_id, events=logger.event)
        logger.metrics = metrics
        target = PostgresTargetAdapter()
        context = MigrationContext(
            clientdir=clientdir, config={}, artifacts=artifacts, sources={'v1': source}, target=target,
            output=FileOutput(artifacts, target), logger=logger, progress=progress, metrics=metrics,
        )
        context.open_phase('setup')
        report_at_manifest = {}
        write_manifest = context.write_manifest

        def recording_write_manifest(status, error=None):
            report_at_manifest[status] = os.path.exists(os.path.join(artifacts.run_dir, GENERATION_REPORT))
            write_manifest(status, error)

        context.write_manifest = recording_write_manifest

        class Migration:
            def run_inserts(self):
                TableMigration(SourceQuery('tb_aluno'), 'students', [
                    Copy('id'), Lookup('status', statuses_map(), v2_name='status_id'),
                ]).run(context.source('v1'))

        with redirect_stdout(StringIO()):
            MigrationRunner(context, Migration()).run()
        context.close()

        def read(path):
            with open(path, encoding='utf-8') as file:
                return file.read()

        return {
            'artifacts': artifacts,
            'report': json.loads(read(os.path.join(artifacts.run_dir, GENERATION_REPORT))),
            'manifest': json.loads(read(artifacts.manifest_path)),
            'log': read(artifacts.log_path('migration.log')),
            'events': [json.loads(line) for line in read(artifacts.log_path('migration.jsonl')).splitlines()],
            'report_at_manifest': report_at_manifest,
        }

    def test_successful_run_writes_the_report_before_the_manifest(self):
        rows = [{'id': 1, 'status': 'A'}, {'id': 2, 'status': 'X'}]
        result = self.run_migration(FakeSqlSource(rows=rows))
        report, manifest, artifacts = result['report'], result['manifest'], result['artifacts']

        self.assertEqual(manifest['status'], 'success')
        self.assertTrue(result['report_at_manifest']['success'])
        self.assertEqual(report['status'], 'success')
        self.assertEqual(report['run_id'], f'Acme/{artifacts.timestamp}')
        self.assertEqual([phase['phase'] for phase in report['phases']],
                         ['setup', 'prepare_target', 'copy_compatible_entities', 'load_transformed_data', 'finalize_target'])
        self.assertEqual(report['units'][0]['phase'], 'load_transformed_data')
        self.assertEqual(report['units'][0]['inserts'], {'students': 2})
        self.assertEqual(report['totals']['losses'], 1)
        for artifact in report['artifacts']:
            self.assertEqual(artifact['bytes'], os.path.getsize(os.path.join(artifacts.run_dir, artifact['path'])))
        loaded = [artifact for artifact in report['artifacts'] if artifact['phase'] == 'load_transformed_data']
        self.assertGreater(loaded[0]['bytes'], 0)
        self.assertIn("[AVISO] students.status_id: 1 valores sem correspondência no mapa student_statuses (ex.: 'X')",
                      result['log'])
        self.assertEqual(set(manifest), {'client', 'timestamp', 'status', 'debug', 'run_dir', 'artifacts',
                                         'logs_dir', 'sources', 'target', 'output'})

    def test_failed_run_reports_the_interrupted_unit(self):
        rows = [{'id': number, 'status': 'A'} for number in range(250)]
        result = self.run_migration(BrokenSource(rows=rows))
        report = result['report']

        self.assertEqual(result['manifest']['status'], 'failed')
        self.assertTrue(result['report_at_manifest']['failed'])
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['error'], 'conexão com a origem perdida')
        self.assertEqual(report['phases'][-1]['phase'], 'load_transformed_data')
        self.assertEqual((report['units'][0]['state'], report['units'][0]['rows_read']), (INTERRUPTED, 250))
        errors = [event for event in result['events'] if event['level'] == 'error']
        self.assertTrue(any('conexão com a origem perdida' in event['message'] for event in errors))
        self.assertRegex(result['log'], r'students \(v1\): 250 linhas lidas, 250 inserts, [\d.]+ s, interrompida')


class FileOutputFinishTest(unittest.TestCase):
    def test_finish_flushes_the_open_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = ArtifactManager(os.path.join(tmpdir, 'Acme'))
            output = FileOutput(artifacts, PostgresTargetAdapter())
            output.open_phase('load_transformed_data')
            output.write(Insert('students', {'id': 1}))

            output.finish(True)
            size = os.path.getsize(artifacts.command_path('load_transformed_data', 'sql'))
            output.close()

            self.assertGreater(size, 0)
            self.assertEqual(size, os.path.getsize(artifacts.command_path('load_transformed_data', 'sql')))


if __name__ == '__main__':
    unittest.main()
