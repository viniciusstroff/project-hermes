## ADDED Requirements

### Requirement: Core exposes no compatibility leftovers
The core package SHALL NOT keep modules or `BaseMigration` helpers whose only role is to forward to the current API, and migration code SHALL obtain source engines only through `context.engine(name)`.

#### Scenario: Legacy engine module removed
- **WHEN** code imports `core.engine`
- **THEN** the import fails because the module does not exist
- **AND** `TableMigration`, `ExpandMigration`, `MultiTargetMigration`, `TargetMigration` and `MigrationEngine` are importable from `core` and from their own modules

#### Scenario: Core exports resolve to current modules
- **WHEN** code reads each name listed in `core.__all__`
- **THEN** every name resolves without importing a compatibility module

#### Scenario: Facade has no private engine or progress helpers
- **WHEN** migration code looks for `_engine`, `_fb_engine`, `_v1_cursor`, `_log_table_progress`, `_setup_tui`, `_teardown_tui`, `_footer_lines`, `_render_progress_footer`, `_reset_progress`, `_finish_progress`, `_log_file` or `_tui_active` on a `BaseMigration`
- **THEN** none of these attributes exists
- **AND** `self.context.engine('pg_v1')` returns the engine for the PostgreSQL V1 source

#### Scenario: Facade keeps its writing helpers
- **WHEN** migration code calls `write_sql()`, `print_comment()`, `_open_sql_file()` or `_flush_writes()` on a `BaseMigration`
- **THEN** the call is delegated to the configured output adapter as before

#### Scenario: Database drivers are imported where connections open
- **WHEN** the default context opens the source and target connections
- **THEN** the drivers are used from `core.context`
- **AND** `core.base_migration` does not import `psycopg2` or `firebirdsql`
