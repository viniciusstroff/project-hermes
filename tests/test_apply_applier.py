import datetime
import json
import os
import pathlib
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
    ArtifactApplier,
    IncompleteRunError,
)
from core.application import Capabilities, CapabilityError
from core.artifacts import ArtifactFormat
from tests.apply_fakes import (
    DEFAULT_FILES,
    COPY,
    FINALIZE,
    LOAD,
    PREPARE,
    SETUP,
    Clock,
    FakeConnection,
    make_run,
    session_of,
)

DUPLICATE = ('duplicate key value violates unique constraint "b_pkey"\nDETAIL:  Key (id)=(5) already exists.', '23505')


def files_with(**overrides):
    files = dict(DEFAULT_FILES)
    names = {'setup': SETUP, 'prepare': PREPARE, 'copy': COPY, 'load': LOAD, 'finalize': FINALIZE}
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
        report = ArtifactApplier(run_dir, session_of(connection), **options).apply()
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


class IncompleteRunTest(ApplierTestCase):
    def test_refuses_unsuccessful_generation_without_connecting(self):
        for status in ('failed', 'running'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmpdir:
                run_dir = make_run(tmpdir, status=status)
                connect = Mock()

                with self.assertRaises(IncompleteRunError) as raised:
                    ArtifactApplier(run_dir, connect).apply()

                self.assertIn(repr(status), str(raised.exception))
                connect.assert_not_called()

    def test_refuses_run_whose_listed_artifact_is_missing_even_in_debug(self):
        # The manifest lists the copy artifact in debug mode too: it copies a limited number of rows.
        for debug in (False, True):
            with self.subTest(debug=debug), tempfile.TemporaryDirectory() as tmpdir:
                files = dict(DEFAULT_FILES)
                del files[COPY]
                run_dir = make_run(tmpdir, files, debug=debug, listed=list(DEFAULT_FILES))
                connect = Mock()

                with self.assertRaises(IncompleteRunError) as raised:
                    ArtifactApplier(run_dir, connect).apply()

                self.assertIn(COPY, str(raised.exception))
                connect.assert_not_called()

    def test_refuses_directory_without_manifest(self):
        connect = Mock()

        with self.assertRaises(IncompleteRunError) as raised:
            ArtifactApplier(self.tmpdir, connect).apply()

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
        run_dir = make_run(self.tmpdir)
        self.assertEqual(ArtifactApplier(run_dir, Mock(), mode=APPLY).on_error, STOP)
        self.assertEqual(ArtifactApplier(run_dir, Mock(), mode=DRY_RUN).on_error, CONTINUE)
        self.assertEqual(ArtifactApplier(run_dir, Mock(), mode=APPLY, on_error=CONTINUE).on_error, CONTINUE)
        self.assertEqual(ArtifactApplier(run_dir, Mock(), mode=DRY_RUN, on_error=STOP).on_error, STOP)

    def test_rejects_invalid_options(self):
        run_dir = make_run(self.tmpdir)
        with self.assertRaises(ValueError):
            ArtifactApplier(run_dir, Mock(), mode='commit')
        with self.assertRaises(ValueError):
            ArtifactApplier(run_dir, Mock(), on_error='ignore')
        with self.assertRaises(ValueError):
            ArtifactApplier(run_dir, Mock(), batch_size=0)


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
        self.assertIn(f'== {LOAD}, comando 5, linha 5, código 23505\n', errors)
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

        report = self.apply(connection, files=files_with(copy=dump))

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

        report = self.apply(connection, files=files_with(copy=dump), mode=DRY_RUN)

        self.assertEqual(report.result, FAILED)
        self.assertIn('COPY ... FROM stdin não é suportado', report.failure)
        self.assertIn(f'{COPY}, comando 1, linha 1', report.failure)
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
            ArtifactApplier(run_dir, session_of(connection)).apply()

        self.assertEqual(connection.log[-1], '<rollback>')
        self.assertEqual(connection.committed, [])
        self.assertTrue(connection.closed)
        (folder,) = os.listdir(os.path.join(run_dir, 'aplicacoes'))
        with open(os.path.join(run_dir, 'aplicacoes', folder, 'relatorio.json'), encoding='utf-8') as report_file:
            saved = json.load(report_file)
        self.assertEqual(saved['result'], FAILED)
        self.assertEqual(saved['failure'], 'aplicação interrompida')
        self.assertIsNotNone(saved['files'][-1]['duration_seconds'])



class SavepointlessTarget:
    """Target adapter that has transactions but no savepoints, like MongoDB."""

    name = 'sem-savepoint'
    artifact_format = ArtifactFormat('sql', 'sql')
    capabilities = Capabilities(transactions=True, savepoints=False, suspend_constraints=False)

    @classmethod
    def connect(cls, config):
        return cls()

    def render(self, operation):
        return []

    def read(self, query):
        return iter(())

    def session(self):
        pass

    def close(self):
        pass


class NoTransactionTarget(SavepointlessTarget):
    """Target adapter without transactions, like a MongoDB server outside a replica set."""

    capabilities = Capabilities(transactions=False, savepoints=False, suspend_constraints=False)


class CapabilitiesTest(ApplierTestCase):
    def test_dry_run_defaults_to_stop_without_savepoints(self):
        run_dir = make_run(self.tmpdir, target=f'{__name__}:SavepointlessTarget')

        self.assertEqual(ArtifactApplier(run_dir, Mock(), mode=DRY_RUN).on_error, STOP)

    def test_continue_policy_needs_savepoints(self):
        run_dir = make_run(self.tmpdir, target=f'{__name__}:SavepointlessTarget')
        connect = Mock()

        with self.assertRaises(CapabilityError) as raised:
            ArtifactApplier(run_dir, connect, mode=APPLY, on_error=CONTINUE)

        self.assertIn('savepoints', str(raised.exception))
        connect.assert_not_called()

    def test_target_without_transactions_is_refused_before_connecting(self):
        run_dir = make_run(self.tmpdir, target=f'{__name__}:NoTransactionTarget')
        connect = Mock()

        for mode in (APPLY, DRY_RUN):
            with self.subTest(mode=mode), self.assertRaises(CapabilityError) as raised:
                ArtifactApplier(run_dir, connect, mode=mode)
            self.assertIn('transações', str(raised.exception))
        connect.assert_not_called()

    def test_session_with_less_than_declared_fails_before_any_command(self):
        connection = FakeConnection()
        session_open = session_of(connection)

        def weaker_session():
            session = session_open()
            session.capabilities = Capabilities(transactions=False, savepoints=False, suspend_constraints=False)
            return session

        run_dir = make_run(self.tmpdir)
        report = ArtifactApplier(run_dir, weaker_session).apply()

        self.assertEqual(report.result, FAILED)
        self.assertIn('transações', report.failure)
        self.assertEqual(connection.statements(), ['<rollback>'])


class NeutralApplierTest(unittest.TestCase):
    def test_applier_has_no_postgresql_rules(self):
        for path in ('core/apply/applier.py', 'core/apply/cli.py', 'core/apply/run.py'):
            source = pathlib.Path(path).read_text(encoding='utf-8')
            with self.subTest(path=path):
                for fragment in ('psycopg2', 'RESET ALL', 'SHOW ', 'ROLLBACK TO', 'RELEASE SAVEPOINT', 'setval',
                                 'pgcode', 'PGHOST', 'splitter'):
                    self.assertFalse(fragment in source, f'{path} contém {fragment!r}')


class ManifestTest(ApplierTestCase):
    def test_artifacts_come_from_the_manifest_in_phase_order(self):
        connection = FakeConnection()
        files = {LOAD: 'INSERT INTO b VALUES (1);\n', SETUP: '-- abertura\n'}

        report = self.apply(connection, files=files)

        self.assertEqual([entry['file'] for entry in report.as_dict()['files']], [SETUP, LOAD])

    def test_refuses_manifest_problems_without_connecting(self):
        cases = {
            'formato': dict(target=f'{__name__}:SavepointlessTarget', files={'00_setup.jsonl': ''}),
            'adapter': dict(target=None),
            'fase desconhecida': dict(files={'09_outra.sql': ''}),
        }
        for expected, options in cases.items():
            with self.subTest(case=expected), tempfile.TemporaryDirectory() as tmpdir:
                run_dir = make_run(tmpdir, **options)
                connect = Mock()

                with self.assertRaises(IncompleteRunError) as raised:
                    ArtifactApplier(run_dir, connect)

                self.assertIn(expected, str(raised.exception))
                connect.assert_not_called()

    def test_refuses_artifact_outside_the_run(self):
        run_dir = make_run(self.tmpdir)
        manifest_path = os.path.join(run_dir, 'manifest.json')
        with open(manifest_path, encoding='utf-8') as manifest_file:
            manifest = json.load(manifest_file)
        manifest['artifacts'][0]['path'] = '../../../segredo.sql'
        with open(manifest_path, 'w', encoding='utf-8') as manifest_file:
            json.dump(manifest, manifest_file)

        with self.assertRaises(IncompleteRunError) as raised:
            ArtifactApplier(run_dir, Mock())

        self.assertIn('fora da execução', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
