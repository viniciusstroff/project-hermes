import datetime
import hashlib
import json
import os
import tempfile
import unittest

from core.application import Command
from core.apply import APPLY, DRY_RUN, ApplicationReport, ArtifactApplier
from tests.apply_fakes import DEFAULT_FILES, Clock, FakeConnection, FakeDatabaseError, make_run, session_of

TARGET = {'adapter': 'postgres', 'host': 'target-db', 'port': '5432', 'database': 'tenant', 'user': 'tenant'}


def fingerprint(run_dir):
    paths = [os.path.join(run_dir, 'manifest.json')]
    paths += [os.path.join(run_dir, 'comandos', name) for name in sorted(os.listdir(os.path.join(run_dir, 'comandos')))]
    result = {}
    for path in paths:
        with open(path, 'rb') as artifact:
            result[path] = hashlib.sha256(artifact.read()).hexdigest()
    return result


class ReportFileTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_report_has_mode_policy_target_timestamps_and_counters(self):
        run_dir = make_run(self.tmpdir)
        clock = Clock(datetime.datetime(2026, 11, 24, 10, 15, 0), datetime.datetime(2026, 11, 24, 10, 17, 30))

        report = ArtifactApplier(run_dir, session_of(FakeConnection()), mode=DRY_RUN, report_clock=clock).apply()

        with open(report.report_path, encoding='utf-8') as report_file:
            raw = report_file.read()
        saved = json.loads(raw)

        self.assertEqual(saved['mode'], DRY_RUN)
        self.assertEqual(saved['on_error'], 'continue')
        self.assertEqual(saved['target'], TARGET)
        self.assertNotIn('segredo', raw)
        self.assertNotIn('password', raw)
        self.assertEqual(saved['started_at'], '2026-11-24T10:15:00')
        self.assertEqual(saved['finished_at'], '2026-11-24T10:17:30')
        self.assertEqual(saved['result'], 'rolled_back')
        self.assertIsNone(saved['failure'])
        self.assertEqual(saved['errors'], 0)
        self.assertEqual([entry['file'] for entry in saved['files']], list(DEFAULT_FILES))
        self.assertEqual(
            saved['files'][2],
            {
                'file': '02_copy_compatible_entities.sql',
                'executed': 2,
                'errors': 0,
                'skipped': {},
                'duration_seconds': saved['files'][2]['duration_seconds'],
            },
        )
        self.assertIsInstance(saved['files'][2]['duration_seconds'], float)

    def test_dry_run_then_apply_get_own_folders_and_keep_artifacts(self):
        run_dir = make_run(self.tmpdir)
        before = fingerprint(run_dir)

        dry_run = ArtifactApplier(
            run_dir, session_of(FakeConnection()), mode=DRY_RUN,
            report_clock=Clock(datetime.datetime(2026, 11, 24, 10, 15, 0)),
        ).apply()
        apply = ArtifactApplier(
            run_dir, session_of(FakeConnection()), mode=APPLY,
            report_clock=Clock(datetime.datetime(2026, 11, 24, 10, 16, 0)),
        ).apply()

        self.assertEqual(
            sorted(os.listdir(os.path.join(run_dir, 'aplicacoes'))),
            ['20261124-101500-dry-run', '20261124-101600-apply'],
        )
        self.assertEqual(os.path.basename(dry_run.directory), '20261124-101500-dry-run')
        self.assertEqual(os.path.basename(apply.directory), '20261124-101600-apply')
        for report in (dry_run, apply):
            self.assertTrue(os.path.isfile(report.report_path))
            self.assertTrue(os.path.isfile(report.errors_path))
        self.assertEqual(fingerprint(run_dir), before)

    def test_application_in_same_second_gets_suffix(self):
        run_dir = make_run(self.tmpdir)
        moment = datetime.datetime(2026, 11, 24, 10, 15, 0)

        first = ApplicationReport(run_dir, DRY_RUN, 'continue', TARGET, clock=Clock(moment))
        second = ApplicationReport(run_dir, DRY_RUN, 'continue', TARGET, clock=Clock(moment))
        first.finish('rolled_back')
        second.finish('rolled_back')

        self.assertEqual(os.path.basename(first.directory), '20261124-101500-dry-run')
        self.assertEqual(os.path.basename(second.directory), '20261124-101500-dry-run-2')


class ErrorsFileTest(unittest.TestCase):
    def test_errors_file_lists_location_code_message_and_truncated_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report = ApplicationReport(tmpdir, DRY_RUN, 'continue', TARGET)
            report.start_file('03_load_transformed_data.sql')
            long_statement = Command('INSERT INTO b VALUES (' + 'x' * 600 + ')', 40)
            short_statement = Command('SELEC 1', 41)

            report.record_error(
                '03_load_transformed_data.sql', 12, long_statement,
                FakeDatabaseError('value too long for type character varying(10)\n', '22001'), '22001',
            )
            report.record_error('03_load_transformed_data.sql', 13, short_statement, RuntimeError('sem código'))
            report.finish('rolled_back')

            with open(report.errors_path, encoding='utf-8') as errors_file:
                content = errors_file.read()

        blocks = content.split('\n\n')
        self.assertTrue(blocks[0].startswith(
            '== 03_load_transformed_data.sql, comando 12, linha 40, código 22001\n'
            'value too long for type character varying(10)\n'
            '-- comando:\n'
            'INSERT INTO b VALUES (xxx'
        ))
        self.assertTrue(blocks[0].endswith(' ... [truncado, 623 caracteres]'))
        self.assertEqual(
            blocks[1],
            '== 03_load_transformed_data.sql, comando 13, linha 41, sem código\n'
            'sem código\n'
            '-- comando:\n'
            'SELEC 1',
        )
        self.assertEqual(report.as_dict()['files'][0]['errors'], 2)
        self.assertEqual(report.error_count, 2)


if __name__ == '__main__':
    unittest.main()
