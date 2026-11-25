from importlib import import_module

from core.adapters import SourceAdapter, TargetAdapter
from core.dialects import Dialect, FirebirdDialect, PostgresDialect
from core.outputs import FileOutput, MirrorOutput, OutputAdapter

_LAZY_EXPORTS = {
    'ArtifactFormat': ('core.artifacts', 'ArtifactFormat'),
    'ArtifactManager': ('core.artifacts', 'ArtifactManager'),
    'Clear': ('core.operations', 'Clear'),
    'Comment': ('core.operations', 'Comment'),
    'DEFAULT_PROGRESS_EVERY': ('core.migration_engine', 'DEFAULT_PROGRESS_EVERY'),
    'DefaultMigrationContextFactory': ('core.context', 'DefaultMigrationContextFactory'),
    'ExpandMigration': ('core.expand_migration', 'ExpandMigration'),
    'FirebirdSourceAdapter': ('core.adapters', 'FirebirdSourceAdapter'),
    'Insert': ('core.operations', 'Insert'),
    'MigrationContext': ('core.context', 'MigrationContext'),
    'MigrationEngine': ('core.migration_engine', 'MigrationEngine'),
    'MigrationLogger': ('core.runtime', 'MigrationLogger'),
    'MigrationProgress': ('core.runtime', 'MigrationProgress'),
    'MigrationRunner': ('core.runner', 'MigrationRunner'),
    'MultiTargetMigration': ('core.multi_target_migration', 'MultiTargetMigration'),
    'Native': ('core.operations', 'Native'),
    'NativeField': ('core.queries', 'NativeField'),
    'NativeQuery': ('core.queries', 'NativeQuery'),
    'PostgresSourceAdapter': ('core.adapters', 'PostgresSourceAdapter'),
    'PostgresTargetAdapter': ('core.adapters', 'PostgresTargetAdapter'),
    'ResetIdentity': ('core.operations', 'ResetIdentity'),
    'ResumeConstraints': ('core.operations', 'ResumeConstraints'),
    'SourceQuery': ('core.queries', 'SourceQuery'),
    'SqlLiteral': ('core.sql', 'SqlLiteral'),
    'SuspendConstraints': ('core.operations', 'SuspendConstraints'),
    'TableMigration': ('core.table_migration', 'TableMigration'),
    'TargetMigration': ('core.multi_target_migration', 'TargetMigration'),
}

__all__ = [
    'ArtifactFormat',
    'ArtifactManager',
    'Clear',
    'Comment',
    'DEFAULT_PROGRESS_EVERY',
    'DefaultMigrationContextFactory',
    'Dialect',
    'ExpandMigration',
    'FirebirdDialect',
    'FileOutput',
    'FirebirdSourceAdapter',
    'Insert',
    'MigrationContext',
    'MigrationEngine',
    'MigrationLogger',
    'MigrationProgress',
    'MigrationRunner',
    'MirrorOutput',
    'MultiTargetMigration',
    'Native',
    'NativeField',
    'NativeQuery',
    'OutputAdapter',
    'PostgresDialect',
    'PostgresSourceAdapter',
    'PostgresTargetAdapter',
    'ResetIdentity',
    'ResumeConstraints',
    'SqlLiteral',
    'SourceAdapter',
    'SourceQuery',
    'SuspendConstraints',
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
