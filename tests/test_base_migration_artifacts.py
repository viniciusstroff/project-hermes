import os
import json
import subprocess
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
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)
sys.modules['psycopg2'].connect = lambda *args, **kwargs: None
sys.modules['psycopg2'].extras = sys.modules['psycopg2.extras']

firebirdsql = types.ModuleType('firebirdsql')
sys.modules.setdefault('firebirdsql', firebirdsql)
sys.modules['firebirdsql'].connect = lambda *args, **kwargs: None

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

from core.base_migration import BaseMigration
from core.compatible import CopyEntity
from core.connections import ConnectionConfigError
from core.operations import Clear, Comment, Insert, Native, ResetIdentity, SuspendConstraints
from core.outputs import MirrorOutput
class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []

    def execute(self, sql):
        self.executed_sql = sql

    def fetchone(self):
        # Only the version queries call it here; None means a server without version.
        return None

    def __iter__(self):
        return iter(self.rows)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []

    def cursor(self, *args, **kwargs):
        return FakeCursor(self.rows)

    def close(self):
        pass


class RecordingOutput:
    def __init__(self):
        self.events = []

    def open_phase(self, phase):
        self.events.append(('open_phase', phase))

    def write(self, operation):
        self.events.append(('write', operation))

    def flush(self):
        self.events.append(('flush',))

    def finish(self, success):
        self.events.append(('finish', success))

    def describe(self):
        return {'type': 'recording'}

    def close(self):
        self.events.append(('close',))


def comment_block(text):
    dashes = '-' * len(text)
    return f'\n-----{dashes}\n-----{text}\n-----{dashes}\n\n'


def read_file(path):
    with open(path, encoding='utf-8') as sql_file:
        return sql_file.read()


INSERT_COUNT = 501
ENV_LINES = [
    'SOURCE_V1_ADAPTER=postgres',
    'SOURCE_V1_HOST=source-db',
    'SOURCE_V1_PORT=5432',
    'SOURCE_V1_NAME=source',
    'SOURCE_V1_USER=source-user',
    'SOURCE_V1_PASS=source-pass',
    'TARGET_ADAPTER=postgres',
    'TARGET_HOST=target-db',
    'TARGET_PORT=5432',
    'TARGET_NAME=target',
    'TARGET_USER=target-user',
    'TARGET_PASS=target-pass',
    'SOURCE_FB_ADAPTER=firebird',
    'SOURCE_FB_HOST=firebird-db',
    'SOURCE_FB_PORT=3050',
    'SOURCE_FB_NAME=firebird',
    'SOURCE_FB_USER=firebird-user',
    'SOURCE_FB_PASS=firebird-pass',
    'SOURCE_FB_CHARSET=ISO8859_1',
]


class FullArtifactMigration(BaseMigration):
    def run_initial(self):
        self.comment('PREPARACAO')
        self.suspend_constraints()

    def run_inserts(self):
        self.comment('INSERTS')
        for index in range(INSERT_COUNT):
            self.emit(Insert('public.demo', {'f_id': index}))
        self.emit(Native('postgres', '-- after inserts'))

    def reset_sequences(self):
        self.reset_identity('public.demo', column='f_id')


class ArtifactMigration(BaseMigration):
    def run_initial(self):
        self.emit(Native('postgres', '-- prepare\n'))

    def run_inserts(self):
        self.emit(Native('postgres', '-- load\n'))

    def reset_sequences(self):
        self.emit(Native('postgres', '-- finalize\n'))



class CopyMigration(BaseMigration):
    def compatible_entities(self):
        return [
            CopyEntity('public.feriados', source='v1'),
            CopyEntity('public.cidades_v1', source='v1', target='public.cidades'),
        ]


class BaseMigrationArtifactsTest(unittest.TestCase):
    def _write_env(self, clientdir):
        with open(os.path.join(clientdir, '.env'), 'w', encoding='utf-8') as env_file:
            env_file.write('\n'.join(ENV_LINES))

    def _clientdir(self, tmpdir):
        clientdir = os.path.join(tmpdir, 'Acme')
        os.makedirs(clientdir)
        self._write_env(clientdir)
        return clientdir

    def test_writes_phase_artifacts_in_comandos(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = self._clientdir(tmpdir)

            with patch('psycopg2.connect', return_value=FakeConnection()), \
                 patch('firebirdsql.connect', return_value=FakeConnection()):
                with redirect_stdout(StringIO()):
                    migration = ArtifactMigration(clientdir)
                self.assertEqual(migration.file.name, migration.artifacts.command_path('setup', 'sql'))

                with redirect_stdout(StringIO()):
                    migration.run()

            commands = os.path.join(migration.artifacts.run_dir, 'comandos')
            self.assertEqual(
                sorted(os.listdir(commands)),
                ['00_setup.sql', '01_prepare_target.sql', '02_copy_compatible_entities.sql',
                 '03_load_transformed_data.sql', '04_finalize_target.sql'],
            )
            self.assertFalse(os.path.exists(os.path.join(migration.artifacts.run_dir, 'sql')))
            self.assertEqual(read_file(os.path.join(commands, '01_prepare_target.sql')), '-- prepare\n')
            with open(migration.artifacts.log_path('migration.log'), encoding='utf-8') as log_file:
                self.assertIn('Artefatos da execução:', log_file.read())
            with open(migration.artifacts.manifest_path, encoding='utf-8') as manifest_file:
                manifest = json.load(manifest_file)

            self.assertEqual(manifest['status'], 'success')
            self.assertFalse(manifest['debug']['enabled'])
            self.assertEqual(manifest['output'], {'type': 'file', 'format': 'sql'})
            self.assertEqual(
                [artifact['path'] for artifact in manifest['artifacts']],
                ['comandos/00_setup.sql', 'comandos/01_prepare_target.sql', 'comandos/02_copy_compatible_entities.sql',
                 'comandos/03_load_transformed_data.sql', 'comandos/04_finalize_target.sql'],
            )

    def _copy_run(self, tmpdir, extra_env=(), debug=None, run=None, server_version=160004):
        clientdir = os.path.join(tmpdir, 'Acme')
        os.makedirs(clientdir)
        with open(os.path.join(clientdir, '.env'), 'w', encoding='utf-8') as env_file:
            env_file.write('\n'.join([*ENV_LINES, *extra_env]))
        source = FakeConnection([{'id': 1, 'nome': 'Natal'}])
        source.server_version = server_version
        with patch('psycopg2.connect', side_effect=[source, FakeConnection()]), \
             patch('firebirdsql.connect', return_value=FakeConnection()), \
             patch('core.adapters.pg_dump.subprocess.run', side_effect=run):
            with redirect_stdout(StringIO()):
                migration = CopyMigration(clientdir)
                if debug is not None:
                    migration.enable_debug(debug)
                migration.run()
        migration.context.close()
        return migration, read_file(migration.artifacts.command_path('copy_compatible_entities', 'sql'))

    def test_compatible_entities_are_copied_row_by_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            migration, copy_sql = self._copy_run(tmpdir)
            with open(migration.artifacts.manifest_path, encoding='utf-8') as manifest_file:
                manifest = json.load(manifest_file)

        self.assertEqual(
            copy_sql,
            "INSERT INTO public.feriados (id, nome) VALUES (1, 'Natal');\n"
            "INSERT INTO public.cidades (id, nome) VALUES (1, 'Natal');\n",
        )
        self.assertEqual(manifest['status'], 'success')

    def test_native_copy_includes_pg_dump_output_after_the_renamed_entities(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            if command[-1] == '--version':
                return subprocess.CompletedProcess(command, 0, stdout='pg_dump (PostgreSQL) 16.4\n', stderr='')
            with open(command[command.index('-f') + 1], 'w', encoding='utf-8') as dump_file:
                dump_file.write("SELECT pg_catalog.set_config('search_path', '', false);\nINSERT INTO public.feriados VALUES (1);\n")
            return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

        with tempfile.TemporaryDirectory() as tmpdir:
            migration, copy_sql = self._copy_run(tmpdir, ['SOURCE_V1_NATIVE_COPY=pg_dump'], run=fake_run)
            leftovers = [name for name in os.listdir(migration.artifacts.run_dir) if name.endswith('.tmp')]

        self.assertEqual(
            copy_sql,
            "INSERT INTO public.cidades (id, nome) VALUES (1, 'Natal');\n"
            "SELECT pg_catalog.set_config('search_path', '', false);\nINSERT INTO public.feriados VALUES (1);\n",
        )
        dump_command, dump_options = calls[1]
        self.assertEqual(dump_command[dump_command.index('-t') + 1], 'public.feriados')
        self.assertEqual(dump_command.count('-t'), 1)
        self.assertIn('-w', dump_command)
        self.assertEqual(dump_options['env']['PGPASSWORD'], 'source-pass')
        self.assertNotIn('source-pass', dump_command)
        self.assertEqual(leftovers, [])

    def test_debug_mode_copies_row_by_row_even_with_native_copy(self):
        def fake_run(command, **kwargs):
            if command[-1] == '--version':
                return subprocess.CompletedProcess(command, 0, stdout='pg_dump (PostgreSQL) 16.4\n', stderr='')
            raise AssertionError('pg_dump não deve rodar no modo debug')

        with tempfile.TemporaryDirectory() as tmpdir:
            _, copy_sql = self._copy_run(tmpdir, ['SOURCE_V1_NATIVE_COPY=pg_dump'], debug=1, run=fake_run)

        self.assertIn('INSERT INTO public.feriados (id, nome)', copy_sql)

    def test_pg_dump_older_than_the_server_stops_before_generation(self):
        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout='pg_dump (PostgreSQL) 9.6.24\n', stderr='')

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ConnectionConfigError) as raised:
                self._copy_run(tmpdir, ['SOURCE_V1_NATIVE_COPY=pg_dump'], run=fake_run, server_version=170002)

        message = str(raised.exception)
        self.assertIn('9.6.24', message)
        self.assertIn('17.2', message)
        self.assertIn('SOURCE_V1_PG_DUMP', message)

    def test_unknown_native_copy_is_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ConnectionConfigError) as raised:
                self._copy_run(tmpdir, ['SOURCE_V1_NATIVE_COPY=pg_dumpall'])

        self.assertIn('SOURCE_V1_NATIVE_COPY=pg_dumpall', str(raised.exception))

    def test_facade_has_no_dump_helpers(self):
        for name in ('dump_tables', 'dump_ignoring_tables', 'dump_non_existing_tables', 'dump_source'):
            with self.subTest(name=name):
                self.assertFalse(hasattr(BaseMigration, name))
        self.assertEqual(BaseMigration.compatible_entities(None), [])

    def test_debug_mode_is_recorded_in_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = self._clientdir(tmpdir)

            with patch('psycopg2.connect', return_value=FakeConnection()), \
                 patch('firebirdsql.connect', return_value=FakeConnection()):
                with redirect_stdout(StringIO()):
                    migration = ArtifactMigration(clientdir)
                    migration.enable_debug(25)

            with open(migration.artifacts.manifest_path, encoding='utf-8') as manifest_file:
                manifest = json.load(manifest_file)

            self.assertEqual(manifest['status'], 'running')
            self.assertEqual(manifest['debug'], {'enabled': True, 'limit': 25})

    def test_inherited_migration_renders_operations_into_phase_files(self):
        catalog = [('public', 'demo')]
        with tempfile.TemporaryDirectory() as tmpdir:
            clientdir = self._clientdir(tmpdir)

            with patch('psycopg2.connect', side_effect=[FakeConnection(), FakeConnection(catalog)]), \
                 patch('firebirdsql.connect', return_value=FakeConnection()):
                with redirect_stdout(StringIO()):
                    migration = FullArtifactMigration(clientdir)
                    migration.emit(Native('postgres', '-- setup\n'))
                    migration.run()
            migration.context.close()

            commands = os.path.join(migration.artifacts.run_dir, 'comandos')
            path = lambda name: os.path.join(commands, name)
            self.assertEqual(read_file(path('00_setup.sql')), '-- setup\n')
            self.assertEqual(
                read_file(path('01_prepare_target.sql')),
                comment_block('PREPARACAO') + 'ALTER TABLE public.demo DISABLE TRIGGER ALL;\n',
            )
            self.assertEqual(
                read_file(path('03_load_transformed_data.sql')),
                comment_block('INSERTS')
                + ''.join(f'INSERT INTO public.demo (f_id) VALUES ({index});\n' for index in range(INSERT_COUNT))
                + '-- after inserts\n',
            )
            self.assertEqual(
                read_file(path('04_finalize_target.sql')),
                "SELECT pg_catalog.setval(pg_catalog.pg_get_serial_sequence('public.demo', 'f_id'), "
                'COALESCE((SELECT MAX(f_id) FROM public.demo), 1));\n'
                + comment_block('HABILITAR TRIGGERS DE TABELAS')
                + 'ALTER TABLE public.demo ENABLE TRIGGER ALL;\n',
            )

    def test_facade_shortcuts_emit_operations_through_the_context(self):
        emitted = []
        context = types.SimpleNamespace(config={}, artifacts=None, emit=emitted.append, close=lambda: None)

        migration = ArtifactMigration('Acme', context=context)
        migration.comment('SECAO')
        migration.clear('public.a')
        migration.suspend_constraints()
        migration.reset_identity(['public.a', 'public.b'], column='f_id')
        migration.emit(Native('postgres', 'VACUUM;'))

        self.assertEqual(emitted, [
            Comment('SECAO'),
            Clear(('public.a',)),
            SuspendConstraints(),
            ResetIdentity('public.a', 'f_id'),
            ResetIdentity('public.b', 'f_id'),
            Native('postgres', 'VACUUM;'),
        ])


if __name__ == '__main__':
    unittest.main()
