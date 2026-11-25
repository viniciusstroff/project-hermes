from importlib import import_module

from core.adapters import SourceAdapter, TargetAdapter
from core.dialects import Dialect, FirebirdDialect, PostgresDialect
from core.metadata import MetadataProvider, PostgresMetadataProvider
from core.outputs import MirrorOutput, OutputAdapter, SqlFileOutput

_LAZY_EXPORTS = {
    'ArtifactManager': ('core.artifacts', 'ArtifactManager'),
    'DEFAULT_PROGRESS_EVERY': ('core.migration_engine', 'DEFAULT_PROGRESS_EVERY'),
    'DefaultMigrationContextFactory': ('core.context', 'DefaultMigrationContextFactory'),
    'ExpandMigration': ('core.expand_migration', 'ExpandMigration'),
    'FirebirdSourceAdapter': ('core.adapters', 'FirebirdSourceAdapter'),
    'MigrationContext': ('core.context', 'MigrationContext'),
    'MigrationEngine': ('core.migration_engine', 'MigrationEngine'),
    'MigrationLogger': ('core.runtime', 'MigrationLogger'),
    'MigrationProgress': ('core.runtime', 'MigrationProgress'),
    'MigrationRunner': ('core.runner', 'MigrationRunner'),
    'MultiTargetMigration': ('core.multi_target_migration', 'MultiTargetMigration'),
    'NativeField': ('core.queries', 'NativeField'),
    'NativeQuery': ('core.queries', 'NativeQuery'),
    'PostgresSourceAdapter': ('core.adapters', 'PostgresSourceAdapter'),
    'PostgresTargetAdapter': ('core.adapters', 'PostgresTargetAdapter'),
    'SourceQuery': ('core.queries', 'SourceQuery'),
    'SqlLiteral': ('core.sql', 'SqlLiteral'),
    'TableMigration': ('core.table_migration', 'TableMigration'),
    'TargetMigration': ('core.multi_target_migration', 'TargetMigration'),
}

__all__ = [
    'ArtifactManager',
    'DEFAULT_PROGRESS_EVERY',
    'DefaultMigrationContextFactory',
    'Dialect',
    'ExpandMigration',
    'FirebirdDialect',
    'FirebirdSourceAdapter',
    'MetadataProvider',
    'MigrationContext',
    'MigrationEngine',
    'MigrationLogger',
    'MigrationProgress',
    'MigrationRunner',
    'MirrorOutput',
    'MultiTargetMigration',
    'NativeField',
    'NativeQuery',
    'OutputAdapter',
    'PostgresDialect',
    'PostgresMetadataProvider',
    'PostgresSourceAdapter',
    'PostgresTargetAdapter',
    'SqlFileOutput',
    'SqlLiteral',
    'SourceAdapter',
    'SourceQuery',
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
