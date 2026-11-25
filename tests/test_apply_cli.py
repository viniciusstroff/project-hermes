import datetime
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import Aplicar
import Migracao
from core.apply import APPLY, CONTINUE, DRY_RUN, STOP, SqlArtifactApplier
from core.apply.cli import (
    MissingTargetError,
    TargetConnectionError,
    config_target,
    libpq_target,
    run_application,
)
from core.adapters.registry import AdapterResolutionError
from core.connections import ConnectionConfigError
from tests.apply_fakes import Clock, FakeConnection, make_run

TARGET = {'host': 'target-db', 'port': '5432', 'database': 'tenant', 'user': 'tenant'}
CONFIG = {
    'SOURCE_V1_ADAPTER': 'postgres',
    'TARGET_ADAPTER': 'postgres',
    'TARGET_HOST': 'target-db',
    'TARGET_PORT': '5432',
    'TARGET_NAME': 'tenant',
    'TARGET_USER': 'tenant',
    'TARGET_PASS': 'segredo',
}


class LibpqTargetTest(unittest.TestCase):
    def test_requires_host_and_database(self):
        with self.assertRaises(MissingTargetError) as raised:
            libpq_target({'PGPORT': '5433'})

        self.assertIn('PGHOST e PGDATABASE', str(raised.exception))

    def test_reads_libpq_variables_with_defaults(self):
        self.assertEqual(
            libpq_target({'PGHOST': 'db', 'PGDATABASE': 'tenant', 'PGUSER': 'carga'}),
            {'host': 'db', 'port': '5432', 'database': 'tenant', 'user': 'carga'},
        )

    def test_config_target_omits_password(self):
        self.assertEqual(config_target(CONFIG), TARGET)


class RunApplicationTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.run_dir = make_run(self._tmpdir.name)
        self.messages = []

    def tearDown(self):
        self._tmpdir.cleanup()

    def run_with(self, connect, mode=APPLY, on_error=None, run_dir=None):
        return run_application(run_dir or self.run_dir, connect, TARGET, mode, on_error, log=self.messages.append)

    def test_shows_target_and_mode_before_applying(self):
        connect = Mock(side_effect=lambda: self.messages.append('<connect>') or FakeConnection())

        code = self.run_with(connect, mode=DRY_RUN)

        self.assertEqual(code, 0)
        self.assertEqual(self.messages[0], 'Destino: tenant@target-db:5432/tenant')
        self.assertEqual(self.messages[1], f'Modo: {DRY_RUN}, política de erro: {CONTINUE}')
        self.assertEqual(self.messages[2], '<connect>')
        self.assertIn('Resultado: rolled_back. ROLLBACK executado, destino sem alterações.', self.messages)

    def test_failed_application_exits_with_error(self):
        connection = FakeConnection(failures={'b VALUES (1)': ('duplicate key', '23505')})

        code = self.run_with(lambda: connection, mode=APPLY)

        self.assertEqual(code, 1)
        self.assertIn('Resultado: failed. ROLLBACK executado, nada da carga foi gravado.', self.messages)
        self.assertTrue(any(message.startswith('Motivo: 03_load_transformed_data.sql') for message in self.messages))

    def test_continue_with_errors_still_exits_with_success(self):
        connection = FakeConnection(failures={'b VALUES (1)': ('duplicate key', '23505')})

        code = self.run_with(lambda: connection, mode=APPLY, on_error=CONTINUE)

        self.assertEqual(code, 0)
        self.assertIn('Comandos com erro: 1', self.messages)

    def test_incomplete_run_exits_without_connecting(self):
        connect = Mock()
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = make_run(tmpdir, status='failed')

            code = self.run_with(connect, run_dir=run_dir)

        self.assertEqual(code, 1)
        connect.assert_not_called()
        self.assertTrue(self.messages[-1].startswith('[ERRO] '))
        self.assertIn("status 'failed'", self.messages[-1])

    def test_connection_error_is_reported_without_traceback(self):
        connect = Mock(side_effect=TargetConnectionError('Não foi possível conectar ao destino: recusado'))

        code = self.run_with(connect)

        self.assertEqual(code, 1)
        self.assertEqual(self.messages[-1], '[ERRO] Não foi possível conectar ao destino: recusado')

    def test_interruption_exits_with_130(self):
        connection = FakeConnection(interrupt_on='b VALUES (1)')

        code = self.run_with(lambda: connection)

        self.assertEqual(code, 130)
        self.assertEqual(self.messages[-1], '[ERRO] Aplicação interrompida. A transação foi desfeita.')
        self.assertEqual(connection.log[-1], '<rollback>')


class ApplierProgressTest(unittest.TestCase):
    def test_logs_each_artifact_and_periodic_progress(self):
        messages = []
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = make_run(tmpdir)
            SqlArtifactApplier(
                run_dir,
                lambda: FakeConnection(),
                log=messages.append,
                progress_every=2,
                report_clock=Clock(datetime.datetime(2026, 11, 24, 10, 0, 0)),
            ).apply()

        self.assertEqual(messages[0], 'Aplicando 00_setup.sql')
        load_start = messages.index('Aplicando 03_load_transformed_data.sql')
        self.assertEqual(messages[load_start + 1], '  03_load_transformed_data.sql: 2 comandos lidos')
        self.assertTrue(messages[load_start + 2].startswith(
            '  03_load_transformed_data.sql: executados 2, com erro 0, pulados 0, '
        ))


class AplicarArgumentsTest(unittest.TestCase):
    def test_defaults_to_apply_without_policy(self):
        args = Aplicar.parse_args(['runs/Acme/20261124-100000'])

        self.assertEqual((args.run_dir, args.dry_run, args.on_error), ('runs/Acme/20261124-100000', False, None))

    def test_dry_run_and_policy(self):
        args = Aplicar.parse_args(['runs/x', '--dry-run', '--on-error', STOP])

        self.assertEqual((args.dry_run, args.on_error), (True, STOP))

    def test_rejects_unknown_policy(self):
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            Aplicar.parse_args(['runs/x', '--on-error', 'ignore'])

    def test_missing_target_exits_with_2(self):
        with patch.dict('os.environ', {}, clear=True), patch('builtins.print') as printed:
            code = Aplicar.main(['runs/x'])

        self.assertEqual(code, 2)
        self.assertIn('PGHOST e PGDATABASE', printed.call_args.args[0])

    def test_main_passes_mode_and_policy(self):
        environ = {'PGHOST': 'db', 'PGDATABASE': 'tenant', 'PGUSER': 'carga'}
        with patch.dict('os.environ', environ, clear=True), patch.object(Aplicar, 'run_application', return_value=0) as run:
            code = Aplicar.main(['runs/x', '--dry-run', '--on-error', STOP])

        self.assertEqual(code, 0)
        run_dir, connect, target, mode, on_error = run.call_args.args
        self.assertEqual((run_dir, target['host'], mode, on_error), ('runs/x', 'db', DRY_RUN, STOP))


class MigracaoArgumentsTest(unittest.TestCase):
    def test_generation_only(self):
        args = Migracao.parse_args(['Acme'])

        self.assertEqual((args.client, args.debug, args.application, args.on_error), ('Acme', None, None, None))

    def test_debug_without_quantity_uses_default(self):
        self.assertEqual(Migracao.parse_args(['Acme', '--debug']).debug, 100)
        self.assertEqual(Migracao.parse_args(['Acme', '--debug', '500']).debug, 500)

    def test_apply_and_dry_run(self):
        self.assertEqual(Migracao.parse_args(['Acme', '--apply']).application, APPLY)
        args = Migracao.parse_args(['Acme', '--debug', '10', '--dry-run', '--on-error', STOP])
        self.assertEqual((args.debug, args.application, args.on_error), (10, DRY_RUN, STOP))

    def test_rejects_apply_with_dry_run_and_policy_without_application(self):
        for argv in (['Acme', '--apply', '--dry-run'], ['Acme', '--on-error', CONTINUE]):
            with self.subTest(argv=argv), patch('sys.stderr'), self.assertRaises(SystemExit):
                Migracao.parse_args(argv)

    def test_unknown_client_exits_with_error(self):
        with patch('builtins.print') as printed:
            code = Migracao.main(['ClienteQueNaoExiste'])

        self.assertEqual(code, 1)
        self.assertIn('Arquivo de migração não encontrado', printed.call_args.args[0])


    def test_connection_config_error_exits_without_traceback(self):
        error = ConnectionConfigError('Conexão SOURCE_V1 (postgres) sem os parâmetros obrigatórios: SOURCE_V1_NAME')
        with patch.object(Migracao, 'load_migration', side_effect=error), patch('builtins.print') as printed:
            code = Migracao.main(['AcmeEdu'])

        self.assertEqual(code, 1)
        printed.assert_called_once_with(f'[ERRO] {error}')

    def test_unknown_adapter_exits_without_traceback(self):
        error = AdapterResolutionError("Adapter 'oracle' desconhecido.")
        with patch.object(Migracao, 'load_migration', side_effect=error), patch('builtins.print') as printed:
            code = Migracao.main(['AcmeEdu'])

        self.assertEqual(code, 1)
        printed.assert_called_once_with(f'[ERRO] {error}')


class MigracaoApplyTest(unittest.TestCase):
    def test_closes_generation_before_applying_with_target_connection(self):
        events = []
        connection = FakeConnection()
        with tempfile.TemporaryDirectory() as tmpdir:
            migration = SimpleNamespace(
                config=CONFIG,
                artifacts=SimpleNamespace(run_dir=make_run(tmpdir)),
                context=SimpleNamespace(close=lambda: events.append('close')),
            )

            def connect_factory(config):
                events.append(('factory', config['TARGET_HOST']))

                def connect():
                    events.append('connect')
                    return connection

                return connect

            messages = []
            with patch.object(Migracao, 'config_connect', side_effect=connect_factory):
                code = Migracao.apply_generated_run(migration, DRY_RUN, log=messages.append)

        self.assertEqual(code, 0)
        self.assertEqual(events, ['close', ('factory', 'target-db'), 'connect'])
        self.assertEqual(messages[0], 'Destino: tenant@target-db:5432/tenant')
        self.assertEqual(connection.log[-1], '<rollback>')


if __name__ == '__main__':
    unittest.main()
