"""Compatibilidade para imports legados do motor de migração."""

from core.adapters import FirebirdCursorAdapter
from core.expand_migration import ExpandMigration
from core.migration_engine import DEFAULT_PROGRESS_EVERY, MigrationEngine
from core.multi_target_migration import MultiTargetMigration, TargetMigration
from core.table_migration import TableMigration
