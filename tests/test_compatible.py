import os
import subprocess
import sys
import tempfile
import types
import unittest

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

from core.adapters.pg_dump import PgDumpCopy, server_version_key, version_key
from core.adapters.postgres import PostgresTargetAdapter
from core.artifacts import ArtifactManager
from core.compatible import CopyEntity, copy_compatible_entities
from core.connections import ConnectionConfig, ConnectionConfigError
from core.context import SourceNotConfiguredError, _check_native_copies
from core.migration_engine import MigrationEngine
from core.operations import Native
from core.outputs import FileOutput, MirrorOutput
from tests.source_fakes import FakeSqlSource, sql_writer

CONFIG = ConnectionConfig('SOURCE_V1', 'v1', 'postgres', {
    'host': 'source-db', 'port': '5433', 'name': 'legado', 'user': 'carga', 'pass': 'segredo',
})


def completed(stdout='', returncode=0, stderr=''):
    return lambda command, **kwargs: subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


class CopyEntityTest(unittest.TestCase):
    def test_target_defaults_to_the_entity(self):
        same = CopyEntity('public.holidays', source='v1', order_by='id')
        renamed = CopyEntity('public.cidades_v1', source='v1', target='public.cidades')

        self.assertEqual((same.target_entity, same.renamed, same.order_by), ('public.holidays', False, ('id',)))
        self.assertEqual((renamed.target_entity, renamed.renamed), ('public.cidades', True))

    def test_source_and_entity_are_required(self):
        with self.assertRaises(TypeError) as raised:
            CopyEntity('public.holidays', source='')
        self.assertIn("source='v1'", str(raised.exception))
        with self.assertRaises(TypeError):
            CopyEntity('', source='v1')

    def test_row_by_row_copy_emits_one_insert_per_row_in_source_order(self):
        source = FakeSqlSource('postgres', rows=[{'id': 2, 'nome': 'b'}, {'id': 1, 'nome': 'a'}])
        writes = []
        progress = []
        engine = MigrationEngine(source, PostgresTargetAdapter(), sql_writer(writes), limit=5,
                                 progress_fn=lambda *args: progress.append(args))

        CopyEntity('public.cidades_v1', source='v1', target='public.cidades', order_by='id').run(engine)

        self.assertEqual(source.queries[-1], 'SELECT * FROM public.cidades_v1 ORDER BY id LIMIT 5')
        self.assertEqual(writes, [
            "INSERT INTO public.cidades (id, nome) VALUES (2, 'b');\n",
            "INSERT INTO public.cidades (id, nome) VALUES (1, 'a');\n",
        ])
        self.assertEqual(progress[-1], ('public.cidades', 2, 2, True))


class FakeContext:
    """Just what copy_compatible_entities uses from the migration context."""

    debug_limit = None

    def __init__(self, sources):
        self.sources = sources
        self.logger = types.SimpleNamespace(print_log=lambda line: None)

    def source(self, name):
        if name not in self.sources:
            raise SourceNotConfiguredError(name, self.sources)
        return MigrationEngine(self.sources[name], PostgresTargetAdapter(), lambda operation: None)


class CopyCompatibleEntitiesTest(unittest.TestCase):
    def _context(self, sources):
        return FakeContext(sources)

    def test_refuses_anything_but_copy_entity(self):
        with self.assertRaises(TypeError):
            copy_compatible_entities(self._context({}), ['public.holidays'])

    def test_unknown_source_names_it(self):
        with self.assertRaises(SourceNotConfiguredError) as raised:
            copy_compatible_entities(self._context({'v1': FakeSqlSource()}), [CopyEntity('a', source='legado')])

        self.assertIn("'legado'", str(raised.exception))


class NativeCopyTargetTest(unittest.TestCase):
    def test_native_copy_needs_a_target_of_its_adapter(self):
        source = types.SimpleNamespace(native_copy=types.SimpleNamespace(name='pg_dump', target_adapter='postgres'))

        _check_native_copies({'v1': CONFIG}, {'v1': source}, types.SimpleNamespace(name='postgres'))
        with self.assertRaises(ConnectionConfigError) as raised:
            _check_native_copies({'v1': CONFIG}, {'v1': source}, types.SimpleNamespace(name='mongodb'))

        self.assertIn('SOURCE_V1_NATIVE_COPY=pg_dump', str(raised.exception))
        self.assertIn("'mongodb'", str(raised.exception))


class PgDumpVersionTest(unittest.TestCase):
    def test_version_keys(self):
        self.assertEqual(version_key(9, 6), 906)
        self.assertEqual(version_key(16, 4), 1600)
        self.assertEqual(server_version_key(90624), 906)
        self.assertEqual(server_version_key(160004), 1600)
        self.assertLess(version_key(9, 6), server_version_key(170002))

    def test_same_or_newer_major_passes(self):
        for output, server in (('pg_dump (PostgreSQL) 9.6.24', 90624), ('pg_dump (PostgreSQL) 17.2 (Debian)', 160004),
                               ('pg_dump (PostgreSQL) 16.0', 160004)):
            with self.subTest(output=output):
                copy = PgDumpCopy(CONFIG, run=completed(output))
                copy.check_version(server, 'x')
                self.assertTrue(copy.version)

    def test_older_major_is_refused(self):
        with self.assertRaises(ConnectionConfigError) as raised:
            PgDumpCopy(CONFIG, binary='/opt/pg96/pg_dump', run=completed('pg_dump (PostgreSQL) 9.6.24')).check_version(
                160004, '16.4')

        message = str(raised.exception)
        self.assertIn('9.6.24', message)
        self.assertIn('16.4', message)
        self.assertIn('/opt/pg96/pg_dump', message)

    def test_missing_or_silent_binary_is_a_configuration_error(self):
        def missing(command, **kwargs):
            raise FileNotFoundError(command[0])

        for run in (missing, completed('', returncode=127, stderr='not found'), completed('algo sem versão')):
            with self.subTest(run=run), self.assertRaises(ConnectionConfigError):
                PgDumpCopy(CONFIG, run=run).check_version(160004, '16.4')

    def test_failed_dump_reports_the_last_error_line(self):
        copy = PgDumpCopy(CONFIG, run=completed(returncode=1, stderr='aviso\npg_dump: error: no matching tables were found'))

        with self.assertRaises(RuntimeError) as raised:
            copy.copy(['public.nao_existe'], '/tmp/x.sql')

        self.assertIn('no matching tables were found', str(raised.exception))


class IncludeFileTest(unittest.TestCase):
    def test_file_output_appends_after_pending_commands_and_mirror_forwards(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = ArtifactManager(os.path.join(tmpdir, 'Acme'), timestamp='20261125-100000')
            second = ArtifactManager(os.path.join(tmpdir, 'Acme'), timestamp='20261125-100001')
            target = PostgresTargetAdapter()
            output = MirrorOutput(FileOutput(first, target), FileOutput(second, target))
            native = os.path.join(tmpdir, 'native.sql')
            with open(native, 'w', encoding='utf-8') as native_file:
                native_file.write('INSERT INTO public.b VALUES (2);\n')

            output.open_phase('copy_compatible_entities')
            output.write(Native('postgres', 'INSERT INTO public.a VALUES (1);'))
            output.include_file(native)
            output.close()

            for artifacts in (first, second):
                with open(artifacts.command_path('copy_compatible_entities', 'sql'), encoding='utf-8') as copy_file:
                    self.assertEqual(copy_file.read(),
                                     'INSERT INTO public.a VALUES (1);\nINSERT INTO public.b VALUES (2);\n')


if __name__ == '__main__':
    unittest.main()
