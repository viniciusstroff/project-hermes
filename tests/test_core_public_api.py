import importlib
import sys
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

import core
import core.base_migration
from core.base_migration import BaseMigration

REMOVED_HELPERS = [
    '_engine',
    '_fb_engine',
    '_v1_cursor',
    '_log_table_progress',
    '_setup_tui',
    '_teardown_tui',
    '_footer_lines',
    '_render_progress_footer',
    '_reset_progress',
    '_finish_progress',
    '_log_file',
    '_tui_active',
]


class CompatibilityLeftoversTest(unittest.TestCase):
    def test_legacy_engine_module_is_gone(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module('core.engine')

    def test_migration_classes_come_from_core_and_their_modules(self):
        sources = {
            'TableMigration': 'core.table_migration',
            'ExpandMigration': 'core.expand_migration',
            'MultiTargetMigration': 'core.multi_target_migration',
            'TargetMigration': 'core.multi_target_migration',
            'MigrationEngine': 'core.migration_engine',
        }
        for name, module in sources.items():
            with self.subTest(name=name):
                self.assertIs(getattr(core, name), getattr(importlib.import_module(module), name))

    def test_every_core_export_resolves(self):
        for name in core.__all__:
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(core, name))
                self.assertNotEqual(getattr(getattr(core, name), '__module__', ''), 'core.engine')

    def test_facade_has_no_private_engine_or_progress_helpers(self):
        for name in REMOVED_HELPERS:
            with self.subTest(name=name):
                self.assertFalse(hasattr(BaseMigration, name))

    def test_facade_writes_only_operations(self):
        for name in ('emit', 'comment', 'clear', 'suspend_constraints', 'reset_identity'):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(BaseMigration, name)))
        for name in ('write_sql', 'print_comment', 'sql_value', '_open_sql_file', '_flush_writes',
                     'set_index_commands_for', 'write_reset_sequences', 'set_trigger_commands'):
            with self.subTest(removed=name):
                self.assertFalse(hasattr(BaseMigration, name))

    def test_metadata_provider_is_gone(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module('core.metadata')
        self.assertNotIn('MetadataProvider', core.__all__)
        self.assertNotIn('SqlFileOutput', core.__all__)

    def test_base_migration_does_not_import_drivers(self):
        module_globals = vars(core.base_migration)
        self.assertNotIn('psycopg2', module_globals)
        self.assertNotIn('firebirdsql', module_globals)


if __name__ == '__main__':
    unittest.main()
