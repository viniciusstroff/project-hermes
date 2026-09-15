from importlib import import_module

from core.adapters import SourceAdapter, SourceCursor, TargetAdapter
from core.dialects import Dialect
from core.metadata import MetadataProvider

_LAZY_EXPORTS = {
    'ArtifactManager': ('core.artifacts', 'ArtifactManager'),
    'DEFAULT_PROGRESS_EVERY': ('core.migration_engine', 'DEFAULT_PROGRESS_EVERY'),
    'ExpandMigration': ('core.expand_migration', 'ExpandMigration'),
    'FirebirdCursorAdapter': ('core.migration_engine', 'FirebirdCursorAdapter'),
    'MigrationEngine': ('core.migration_engine', 'MigrationEngine'),
    'MultiTargetMigration': ('core.multi_target_migration', 'MultiTargetMigration'),
    'TableMigration': ('core.table_migration', 'TableMigration'),
    'TargetMigration': ('core.multi_target_migration', 'TargetMigration'),
}

__all__ = [
    'ArtifactManager',
    'DEFAULT_PROGRESS_EVERY',
    'Dialect',
    'ExpandMigration',
    'FirebirdCursorAdapter',
    'MetadataProvider',
    'MigrationEngine',
    'MultiTargetMigration',
    'SourceAdapter',
    'SourceCursor',
    'TableMigration',
    'TargetAdapter',
    'TargetMigration',
]


def __getattr__(name):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

    module_name, attr_name = _LAZY_EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
