import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import Mock

from core.apply import (
    APPLY,
    COMMITTED,
    CONTINUE,
    DRY_RUN,
    FAILED,
    ROLLED_BACK,
    STOP,
    IncompleteRunError,
    SqlArtifactApplier,
)
from tests.apply_fakes import (
    DEFAULT_FILES,
    DUMP,
    FINALIZE,
    LOAD,
    PREPARE,
    SETUP,
    Clock,
    FakeConnection,
    make_run,
)

DUPLICATE = ('duplicate key value violates unique constraint "b_pkey"\nDETAIL:  Key (id)=(5) already exists.', '23505')


def files_with(**overrides):
    files = dict(DEFAULT_FILES)
    names = {'setup': SETUP, 'prepare': PREPARE, 'dump': DUMP, 'load': LOAD, 'finalize': FINALIZE}
    for key, content in overrides.items():
        files[names[key]] = content
    return files


def inserts(count):
    return ''.join(f'INSERT INTO b VALUES ({number});\n' for number in range(1, count + 1))


class ApplierTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def apply(self, connection, files=None, **options):
        run_dir = make_run(self.tmpdir, files)
        options.setdefault('report_clock', Clock(datetime.datetime(2026, 11, 24, 10, 15, 0)))
        report = SqlArtifactApplier(run_dir, lambda: connection, **options).apply()
        return report


class PhaseOrderTest(ApplierTestCase):
    def test_applies_artifacts_in_phase_order_and_commits(self):
        connection = FakeConnection()

        report = self.apply(connection)

        self.assertEqual(report.result, COMMITTED)
        self.assertFalse(connection.autocommit)
        self.assertTrue(connection.closed)
        self.assertEqual(
            connection.statements(),
            [
                'RESET ALL',
                'TRUNCATE a',
                'RESET ALL',
                "SELECT pg_catalog.set_config('search_path', '', false)",
                'INSERT INTO public.a VALUES (1)',
                'RESET ALL',
                'INSERT INTO b VALUES (1)',
                'INSERT INTO b VALUES (2)',
                'RESET ALL',
                "SELECT pg_catalog.setval('a_f_id_seq', 1)",
                'RESET ALL',
                '<commit>',
            ],
        )
        self.assertEqual(len(connection.committed), 6)

    def test_debug_run_without_dump_applies_other_artifacts(self):
        files = dict(DEFAULT_FILES)
        del files[DUMP]
        run_dir = make_run(self.tmpdir, files, debug=True)
        connection = FakeConnection()

        report = SqlArtifactApplier(run_dir, lambda: connection).apply()

        self.assertEqual(report.result, COMMITTED)
        self.assertEqual([entry['file'] for entry in report.as_dict()['files']], [SETUP, PREPARE, LOAD, FINALIZE])
        self.assertEqual(connection.statements().count('RESET ALL'), 4)


class IncompleteRunTest(ApplierTestCase):
    def test_refuses_unsuccessful_generation_without_connecting(self):
        for status in ('failed', 'running'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmpdir:
                run_dir = make_run(tmpdir, status=status)
                connect = Mock()

                with self.assertRaises(IncompleteRunError) as raised:
                    SqlArtifactApplier(run_dir, connect).apply()

                self.assertIn(repr(status), str(raised.exception))
                connect.assert_not_called()

    def test_refuses_full_run_without_dump(self):
        files = dict(DEFAULT_FILES)
        del files[DUMP]
        run_dir = make_run(self.tmpdir, files)
        connect = Mock()

        with self.assertRaises(IncompleteRunError) as raised:
            SqlArtifactApplier(run_dir, connect).apply()

        self.assertIn(DUMP, str(raised.exception))
        connect.assert_not_called()

    def test_refuses_directory_without_manifest(self):
        connect = Mock()

        with self.assertRaises(IncompleteRunError) as raised:
            SqlArtifactApplier(self.tmpdir, connect).apply()

        self.assertIn('manifest.json', str(raised.exception))
        connect.assert_not_called()


class ModeTest(ApplierTestCase):
    def test_dry_run_rolls_back_and_skips_setval(self):
        connection = FakeConnection()

        report = self.apply(connection, mode=DRY_RUN)

        self.assertEqual(report.result, ROLLED_BACK)
        self.assertEqual(connection.log[-1], '<rollback>')
        self.assertNotIn('<commit>', connection.log)
        self.assertEqual(connection.committed, [])
        self.assertFalse(any('setval' in sql for sql in connection.log))
        finalize = report.as_dict()['files'][-1]
        self.assertEqual(finalize['skipped'], {'sequence_change': 1})
        self.assertEqual(finalize['executed'], 0)

    def test_default_policies(self):
        self.assertEqual(SqlArtifactApplier('run', Mock(), mode=APPLY).on_error, STOP)
        self.assertEqual(SqlArtifactApplier('run', Mock(), mode=DRY_RUN).on_error, CONTINUE)
        self.assertEqual(SqlArtifactApplier('run', Mock(), mode=APPLY, on_error=CONTINUE).on_error, CONTINUE)
        self.assertEqual(SqlArtifactApplier('run', Mock(), mode=DRY_RUN, on_error=STOP).on_error, STOP)

    def test_rejects_invalid_options(self):
        with self.assertRaises(ValueError):
            SqlArtifactApplier('run', Mock(), mode='commit')
        with self.assertRaises(ValueError):
            SqlArtifactApplier('run', Mock(), on_error='ignore')
        with self.assertRaises(ValueError):
            SqlArtifactApplier('run', Mock(), batch_size=0)


class StopPolicyTest(ApplierTestCase):
    def test_first_error_rolls_back_everything(self):
        connection = FakeConnection(failures={'b VALUES (2)': DUPLICATE})

        report = self.apply(connection, files=files_with(load=inserts(3)))

        self.assertEqual(report.result, FAILED)
        self.assertEqual(connection.log[-1], '<rollback>')
        self.assertEqual(connection.committed, [])
        self.assertNotIn('INSERT INTO b VALUES (3)', connection.log)
        self.assertNotIn(FINALIZE, [entry['file'] for entry in report.as_dict()['files']])
        self.assertTrue(report.failure.startswith(f'{LOAD}, comando 2, linha 2: duplicate key value'))
        self.assertEqual(report.error_count, 1)


class ContinuePolicyTest(ApplierTestCase):
    def test_failing_statement_is_rolled_back_and_batch_is_replayed(self):
        connection = FakeConnection(failures={'b VALUES (5)': DUPLICATE})

        report = self.apply(connection, files=files_with(load=inserts(5)), on_error=CONTINUE, batch_size=3)

        self.assertEqual(report.result, COMMITTED)
        loaded = [sql for sql in connection.committed if sql.startswith('INSERT INTO b')]
        self.assertEqual(loaded, [f'INSERT INTO b VALUES ({number})' for number in range(1, 5)])

        load_log = connection.log[connection.log.index('INSERT INTO b VALUES (1)') - 1:]
        load_log = load_log[:load_log.index('RESET ALL')]
        self.assertEqual(
            load_log,
            [
                'SAVEPOINT hermes_lote',
                'INSERT INTO b VALUES (1)',
                'INSERT INTO b VALUES (2)',
                'INSERT INTO b VALUES (3)',
                'RELEASE SAVEPOINT hermes_lote',
                'SAVEPOINT hermes_lote',
                'INSERT INTO b VALUES (4)',
                'INSERT INTO b VALUES (5)',
                'ROLLBACK TO SAVEPOINT hermes_lote',
                'SAVEPOINT hermes_comando',
                'INSERT INTO b VALUES (4)',
                'RELEASE SAVEPOINT hermes_comando',
                'SAVEPOINT hermes_comando',
                'INSERT INTO b VALUES (5)',
                'ROLLBACK TO SAVEPOINT hermes_comando',
                'RELEASE SAVEPOINT hermes_comando',
                'RELEASE SAVEPOINT hermes_lote',
            ],
        )

        load = report.as_dict()['files'][3]
        self.assertEqual((load['file'], load['executed'], load['errors']), (LOAD, 4, 1))
        with open(report.errors_path, encoding='utf-8') as errors_file:
            errors = errors_file.read()
        self.assertIn(f'== {LOAD}, comando 5, linha 5, SQLSTATE 23505\n', errors)
        self.assertIn('duplicate key value violates unique constraint "b_pkey"', errors)
        self.assertIn('INSERT INTO b VALUES (5)', errors)

    def test_next_statement_after_failure_opens_new_batch(self):
        connection = FakeConnection(failures={'b VALUES (2)': DUPLICATE, 'b VALUES (4)': DUPLICATE})

        report = self.apply(connection, files=files_with(load=inserts(5)), on_error=CONTINUE, batch_size=10)

        loaded = [sql for sql in connection.committed if sql.startswith('INSERT INTO b')]
        self.assertEqual(loaded, ['INSERT INTO b VALUES (1)', 'INSERT INTO b VALUES (3)', 'INSERT INTO b VALUES (5)'])
        self.assertEqual(report.error_count, 2)
        self.assertEqual(report.result, COMMITTED)

    def test_dry_run_lists_every_error(self):
        connection = FakeConnection(failures={'b VALUES (1)': DUPLICATE, 'b VALUES (3)': DUPLICATE})

        report = self.apply(connection, files=files_with(load=inserts(3)), mode=DRY_RUN)

        self.assertEqual(report.result, ROLLED_BACK)
        self.assertEqual(report.error_count, 2)
        self.assertEqual(connection.committed, [])


class SkippedStatementTest(ApplierTestCase):
    def test_skips_transaction_control_and_meta_commands(self):
        dump = (
            '\\restrict chave\n'
            'BEGIN;\n'
            "SELECT pg_catalog.lo_open('16385', 131072);\n"
            'COMMIT;\n'
            'START TRANSACTION;\n'
            'END;\n'
            '\\unrestrict chave\n'
        )
        connection = FakeConnection()

        report = self.apply(connection, files=files_with(dump=dump))

        self.assertEqual(report.result, COMMITTED)
        executed = connection.statements()
        for statement in ('BEGIN', 'COMMIT', 'START TRANSACTION', 'END'):
            self.assertNotIn(statement, executed)
        self.assertFalse(any(sql.startswith('\\') for sql in executed))
        dump_counters = report.as_dict()['files'][2]
        self.assertEqual(dump_counters['skipped'], {'meta_command': 2, 'transaction_control': 4})
        self.assertEqual(dump_counters['executed'], 1)

    def test_setval_runs_in_apply_mode(self):
        connection = FakeConnection()

        self.apply(connection)

        self.assertIn("SELECT pg_catalog.setval('a_f_id_seq', 1)", connection.committed)

    def test_copy_from_stdin_fails_the_application(self):
        dump = 'COPY public.a (id) FROM stdin;\n1\n\\.\n'
        connection = FakeConnection()

        report = self.apply(connection, files=files_with(dump=dump), mode=DRY_RUN)

        self.assertEqual(report.result, FAILED)
        self.assertIn('COPY ... FROM stdin não é suportado', report.failure)
        self.assertIn(f'{DUMP}, comando 1, linha 1', report.failure)
        self.assertEqual(connection.log[-1], '<rollback>')


class SessionStateTest(ApplierTestCase):
    def test_server_setting_drives_the_splitter(self):
        connection = FakeConnection(standard_conforming_strings='off')

        self.apply(connection, files=files_with(load="INSERT INTO b VALUES ('a\\';b');\n"))

        self.assertIn("INSERT INTO b VALUES ('a\\';b')", connection.committed)


class FailureTest(ApplierTestCase):
    def test_unterminated_literal_fails_the_application(self):
        connection = FakeConnection()

        report = self.apply(connection, files=files_with(load="INSERT INTO b VALUES (1);\nINSERT INTO b VALUES ('x);\n"))

        self.assertEqual(report.result, FAILED)
        self.assertIn(LOAD, report.failure)
        self.assertIn('linha 2', report.failure)
        self.assertEqual(connection.committed, [])

    def test_commit_failure_is_reported(self):
        connection = FakeConnection(commit_error='insert or update violates foreign key constraint "fk_b"')

        report = self.apply(connection)

        self.assertEqual(report.result, FAILED)
        self.assertTrue(report.failure.startswith('COMMIT falhou: insert or update violates foreign key'))
        self.assertEqual(connection.committed, [])

    def test_interruption_rolls_back_writes_report_and_propagates(self):
        connection = FakeConnection(interrupt_on='b VALUES (2)')
        run_dir = make_run(self.tmpdir)

        with self.assertRaises(KeyboardInterrupt):
            SqlArtifactApplier(run_dir, lambda: connection).apply()

        self.assertEqual(connection.log[-1], '<rollback>')
        self.assertEqual(connection.committed, [])
        self.assertTrue(connection.closed)
        (folder,) = os.listdir(os.path.join(run_dir, 'aplicacoes'))
        with open(os.path.join(run_dir, 'aplicacoes', folder, 'relatorio.json'), encoding='utf-8') as report_file:
            saved = json.load(report_file)
        self.assertEqual(saved['result'], FAILED)
        self.assertEqual(saved['failure'], 'aplicação interrompida')
        self.assertIsNotNone(saved['files'][-1]['duration_seconds'])


if __name__ == '__main__':
    unittest.main()
