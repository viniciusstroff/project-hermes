import json
import os
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

from core.artifacts import ArtifactManager
from core.base_migration import BaseMigration
from core.context import MigrationContext
from core.metadata import PostgresMetadataProvider
from core.references import (
    LoadedReferenceMap,
    ReferenceMap,
    ReferenceMapError,
    build_reference_map,
)
from core.runner import MigrationRunner
from core.runtime import MigrationLogger, MigrationProgress
from core.strategies import Lookup


def upper_key(value):
    return str(value).strip().upper()


class DeclarationTest(unittest.TestCase):
    def test_table_declaration(self):
        declaration = ReferenceMap('states', table='public.states', key='f_code', value='f_id',
                                   filters={'f_active': True})
        self.assertEqual((declaration.table, declaration.key, declaration.value), ('public.states', 'f_code', 'f_id'))

    def test_invalid_declarations_name_the_map(self):
        invalid = [
            lambda: ReferenceMap('both', table='t', key='k', value='v', query='SELECT 1, 2'),
            lambda: ReferenceMap('neither'),
            lambda: ReferenceMap('no_key', table='t', value='v'),
            lambda: ReferenceMap('query_filters', query='SELECT 1, 2', filters={'a': 1}),
        ]
        for build in invalid:
            with self.subTest(build=build):
                with self.assertRaises(ReferenceMapError) as raised:
                    build()
                self.assertIn("'", str(raised.exception))

    def test_declaration_is_immutable(self):
        declaration = ReferenceMap('states', table='t', key='k', value='v')
        with self.assertRaises(Exception):
            declaration.name = 'outro'


class BuildReferenceMapTest(unittest.TestCase):
    def test_default_normalization_and_null_keys(self):
        loaded = build_reference_map(
            ReferenceMap('states', table='t', key='k', value='v'),
            [(' SP ', 25), (None, 99), ('RJ', 19)],
        )

        self.assertEqual(dict(loaded), {'SP': 25, 'RJ': 19})
        self.assertEqual(loaded.name, 'states')
        self.assertEqual(loaded.normalize_key(' MG '), 'MG')

    def test_null_keys_kept_when_asked(self):
        loaded = build_reference_map(
            ReferenceMap('states', table='t', key='k', value='v', skip_null_keys=False,
                         key_normalizer=lambda value: value),
            [(None, 99)],
        )
        self.assertEqual(dict(loaded), {None: 99})

    def test_value_normalizer(self):
        loaded = build_reference_map(
            ReferenceMap('states', table='t', key='k', value='v', value_normalizer=int),
            [('SP', '25')],
        )
        self.assertEqual(loaded['SP'], 25)

    def test_ambiguous_key_is_rejected(self):
        with self.assertRaises(ReferenceMapError) as raised:
            build_reference_map(
                ReferenceMap('states', table='t', key='k', value='v', key_normalizer=upper_key),
                [('sp', 25), ('SP ', 31)],
            )
        message = str(raised.exception)
        for part in ("'states'", "'SP'", '25', '31'):
            self.assertIn(part, message)

    def test_repeated_key_with_same_value_is_accepted(self):
        loaded = build_reference_map(
            ReferenceMap('states', table='t', key='k', value='v', key_normalizer=upper_key),
            [('sp', 25), ('SP ', 25)],
        )
        self.assertEqual(dict(loaded), {'SP': 25})

    def test_loaded_map_is_read_only_mapping(self):
        loaded = LoadedReferenceMap('states', {'SP': 25}, upper_key)

        self.assertEqual((loaded['SP'], loaded.get('RJ'), 'SP' in loaded, len(loaded)), (25, None, True, 1))
        with self.assertRaises(TypeError):
            loaded['RJ'] = 19
        self.assertFalse(hasattr(loaded, 'update'))


class LookupWithLoadedMapTest(unittest.TestCase):
    def test_lookup_reuses_the_map_normalizer(self):
        loaded = LoadedReferenceMap('states', {'SP': 25}, upper_key)

        self.assertEqual(Lookup('f_state', loaded).raw_value({'f_state': ' sp '}), 25)

    def test_explicit_normalizer_wins(self):
        loaded = LoadedReferenceMap('states', {'SP': 25}, upper_key)
        strategy = Lookup('f_state', loaded, fallback=-1, key_normalizer=lambda value: value)

        self.assertEqual(strategy.raw_value({'f_state': ' sp '}), -1)
        self.assertEqual(strategy.raw_value({'f_state': 'SP'}), 25)

    def test_plain_dict_keeps_default_strip(self):
        self.assertEqual(Lookup('f_state', {'SP': 25}).raw_value({'f_state': ' SP '}), 25)
        self.assertIsNone(Lookup('f_state', {'SP': 25}).raw_value({'f_state': ' sp '}))


class LegacyLoaderRemovedTest(unittest.TestCase):
    def test_no_ad_hoc_loader(self):
        self.assertFalse(hasattr(MigrationContext, 'load_reference_map'))
        self.assertFalse(hasattr(BaseMigration, 'load_reference_map'))
        self.assertFalse(hasattr(PostgresMetadataProvider, 'load_reference_map'))
        migration = BaseMigration('Acme', context=types.SimpleNamespace(
            config={}, artifacts=None, legacy_connections={}, close=lambda: None))
        self.assertFalse(hasattr(migration, '_reference_maps'))
        self.assertEqual(migration.declare_reference_maps(), [])


class FakeMetadata:
    def __init__(self, events, pairs=None, fail=None):
        self.events = events
        self.pairs = pairs or {}
        self.fail = fail

    def disable_triggers_sql(self):
        return ''

    def enable_triggers_sql(self):
        return ''

    def load_reference_pairs(self, reference):
        self.events.append(('load', reference.name))
        if self.fail == reference.name:
            raise RuntimeError('relation "public.nao_existe" does not exist')
        return self.pairs.get(reference.name, [])

    def close_reads(self):
        self.events.append(('close_reads',))


class RecordingOutput:
    def __init__(self, events):
        self.events = events

    def open_phase(self, phase):
        self.events.append(('open_phase', phase))

    def write(self, sql):
        pass

    def comment(self, text):
        pass

    def flush(self):
        pass

    def finish(self, success):
        self.events.append(('finish', success))

    def describe(self):
        return {'type': 'recording'}

    def close(self):
        pass


class ReferenceMapsRunnerTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.events = []

    def tearDown(self):
        self._tmpdir.cleanup()

    def run_migration(self, declarations, pairs=None, fail=None, uses=None):
        clientdir = os.path.join(self._tmpdir.name, 'Acme')
        os.makedirs(clientdir, exist_ok=True)
        artifacts = ArtifactManager(clientdir)
        logger = MigrationLogger(artifacts)
        progress = MigrationProgress(logger)
        logger.progress = progress
        context = MigrationContext(
            clientdir=clientdir, config={}, artifacts=artifacts, sources={}, target=None,
            metadata=FakeMetadata(self.events, pairs, fail), output=RecordingOutput(self.events),
            logger=logger, progress=progress,
        )
        events = self.events
        used = {}

        class Migration:
            def declare_reference_maps(self):
                return declarations() if callable(declarations) else declarations

            def run_inserts(self):
                events.append(('run_inserts',))
                for name in uses or []:
                    used[name] = dict(context.reference_map(name))

        with redirect_stdout(StringIO()):
            MigrationRunner(context, Migration()).run()
        context.close()
        with open(artifacts.manifest_path, encoding='utf-8') as manifest_file:
            manifest = json.load(manifest_file)
        with open(artifacts.log_path('migration.log'), encoding='utf-8') as log_file:
            log = log_file.read()
        return manifest, log, used

    def test_maps_load_before_prepare_target_and_reads_close(self):
        declarations = [
            ReferenceMap('states', table='public.states', key='f_code', value='f_id'),
            ReferenceMap('areas', table='public.areas', key='f_code', value='f_id'),
        ]
        manifest, log, used = self.run_migration(
            declarations, pairs={'states': [('SP', 25), ('RJ', 19)], 'areas': []}, uses=['states'],
        )

        self.assertEqual(manifest['status'], 'success')
        self.assertEqual(self.events[:4], [('load', 'states'), ('load', 'areas'), ('close_reads',),
                                           ('open_phase', 'prepare_target')])
        self.assertEqual(used['states'], {'SP': 25, 'RJ': 19})
        self.assertIn('Mapa de referência states: 2 chaves', log)
        self.assertIn('[AVISO] Mapa de referência areas carregou vazio', log)

    def test_run_without_declared_maps_still_closes_reads(self):
        manifest, _, _ = self.run_migration([])

        self.assertEqual(manifest['status'], 'success')
        self.assertEqual(self.events[0], ('close_reads',))

    def test_load_error_fails_the_run_before_prepare_target(self):
        manifest, _, _ = self.run_migration(
            [ReferenceMap('states', table='public.nao_existe', key='k', value='v')], fail='states',
        )

        self.assertEqual(manifest['status'], 'failed')
        self.assertIn("Reference map 'states' failed to load", manifest['error'])
        self.assertNotIn(('open_phase', 'prepare_target'), self.events)

    def test_repeated_and_invalid_declarations_fail_before_prepare_target(self):
        cases = {
            'repeated': lambda: [ReferenceMap('states', table='t', key='k', value='v'),
                                 ReferenceMap('states', table='u', key='k', value='v')],
            'invalid': lambda: [ReferenceMap('states', table='t', key='k', value='v', query='SELECT 1, 2')],
        }
        for name, declarations in cases.items():
            with self.subTest(case=name):
                self.events.clear()
                manifest, _, _ = self.run_migration(declarations)
                self.assertEqual(manifest['status'], 'failed')
                self.assertIn('states', manifest['error'])
                self.assertNotIn(('open_phase', 'prepare_target'), self.events)

    def test_undeclared_map_is_an_error(self):
        manifest, _, _ = self.run_migration([], uses=['unknown'])

        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(manifest['error'], "Reference map 'unknown' was not declared in declare_reference_maps()")


if __name__ == '__main__':
    unittest.main()
