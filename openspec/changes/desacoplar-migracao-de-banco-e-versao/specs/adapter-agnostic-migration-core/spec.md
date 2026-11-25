## MODIFIED Requirements

### Requirement: Core consumes source adapters
The migration core SHALL read source data only through the `read(query)` and `count(query)` methods of a `SourceAdapter`, with a `SourceQuery` or a `NativeQuery`, and SHALL NOT import database drivers or call driver APIs.

#### Scenario: Table migration reads through a neutral query
- **WHEN** a `TableMigration` runs with `source=SourceQuery('tb_aluno')`
- **THEN** the engine obtains rows from the `read()` of the configured source adapter
- **AND** the requested fields come from the strategies of the migration

#### Scenario: Same migration on two source databases
- **WHEN** the same `TableMigration` runs once on a PostgreSQL source and once on a MongoDB source holding the same data
- **THEN** both runs emit the same write operations

#### Scenario: Core loads without drivers
- **WHEN** the core modules are imported in an environment without `psycopg2`, `firebirdsql` and `pymongo`
- **THEN** the import succeeds

### Requirement: Source adapters own source-specific query behavior
The source adapter SHALL translate a `SourceQuery`, including its integer limit, into its own query language, and SHALL refuse a `NativeQuery` or a `NativeField` declared for another adapter before reading anything.

#### Scenario: Debug limit
- **WHEN** debug mode sets a limit of 100
- **THEN** the context applies the limit to every query as an integer
- **AND** each adapter renders it in its own syntax

#### Scenario: Native query for another adapter
- **WHEN** a `NativeQuery` declared for `postgres` runs on a `mongodb` source
- **THEN** the migration fails before reading
- **AND** the error names both adapters

#### Scenario: Nested field on a relational source
- **WHEN** a `SourceQuery` asks a `postgres` source for the field `endereco.cidade`
- **THEN** the adapter refuses the query

### Requirement: Core consumes target adapters
Migrations SHALL express target writes as operations (`Insert`, `Clear`, `SuspendConstraints`, `ResumeConstraints`, `ResetIdentity`, `Native` and `Comment`), and the target adapter SHALL translate each operation into zero or more commands of its artifact format.

#### Scenario: Table migration emits inserts
- **WHEN** a `TableMigration` processes a source row
- **THEN** it emits one `Insert` with the target entity and the field values
- **AND** it does not build SQL text

#### Scenario: Multi-target migration emits inserts
- **WHEN** a `MultiTargetMigration` processes a source row with multiple eligible targets
- **THEN** it emits one `Insert` per eligible target

#### Scenario: Operation without meaning in the target
- **WHEN** a `ResetIdentity` operation is rendered by the `mongodb` adapter
- **THEN** the adapter returns no command

#### Scenario: Native operation for another adapter
- **WHEN** a `Native` operation declared for `postgres` reaches the `mongodb` adapter
- **THEN** generation fails with an error naming both adapters

### Requirement: Target dialect owns value serialization
The target adapter, through its dialect when it has one, SHALL serialize Python values into its own format and SHALL refuse a value it cannot represent, naming the entity, the field and the Python type.

#### Scenario: Strategy returns string with quotes
- **WHEN** a field strategy produces a Python string containing a single quote
- **THEN** the PostgreSQL adapter doubles the quote in the SQL literal
- **AND** the MongoDB adapter writes the string unchanged in the JSON document

#### Scenario: Strategy returns null value
- **WHEN** a field strategy produces a null Python value
- **THEN** each target adapter writes its own null representation

### Requirement: Base migration exposes adapter-based context
`BaseMigration` SHALL build its sources and its target from the named connections of the client `.env` through the adapter registry, and SHALL expose each source by its name.

#### Scenario: Migration requests a named source
- **WHEN** a migration calls `self.context.source('v1')`
- **THEN** it receives a migration engine backed by the source adapter declared as `SOURCE_V1_ADAPTER` and by the configured target adapter

#### Scenario: Client without Firebird
- **WHEN** the `.env` declares no Firebird connection
- **THEN** no Firebird connection is attempted and the `firebirdsql` driver is not imported

### Requirement: Core exposes no compatibility leftovers
The core package SHALL NOT keep modules or `BaseMigration` helpers whose only role is to forward to the current API, and migration code SHALL obtain source engines only through `context.source(name)`.

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
- **AND** `self.context.source('v1')` returns the engine for the source declared as `SOURCE_V1_ADAPTER`

#### Scenario: Context has no engine by fixed name
- **WHEN** migration code calls `context.engine('pg_v1')`
- **THEN** the call fails because the context exposes sources only through `source(name)`

#### Scenario: Facade writes only operations
- **WHEN** migration code looks for `write_sql()`, `print_comment()`, `sql_value()`, `_open_sql_file()` or `_flush_writes()` on a `BaseMigration`
- **THEN** none of them exists
- **AND** `emit()`, `comment()`, `clear()`, `suspend_constraints()` and `reset_identity()` deliver operations through `context.emit()`

#### Scenario: Database drivers are imported where connections open
- **WHEN** the default context opens the source and target connections
- **THEN** each adapter imports its driver inside its `connect`
- **AND** neither `core.context` nor `core.base_migration` imports `psycopg2` or `firebirdsql`

## ADDED Requirements

### Requirement: Target maintenance is expressed as operations
The core SHALL offer `Clear`, `SuspendConstraints`, `ResumeConstraints` and `ResetIdentity` as operations with facade shortcuts in `BaseMigration`, and the PostgreSQL target adapter SHALL find the sequence of a column with `pg_get_serial_sequence` instead of a naming convention.

#### Scenario: Reset identity on any column name
- **WHEN** `ResetIdentity('people', 'id')` is rendered by the PostgreSQL adapter
- **THEN** the command uses `pg_get_serial_sequence('people', 'id')`
- **AND** it does not assume a column named `f_id` nor a sequence named `people_id_seq`

#### Scenario: Suspend constraints on PostgreSQL
- **WHEN** `SuspendConstraints()` is rendered by the PostgreSQL adapter
- **THEN** it produces one `ALTER TABLE ... DISABLE TRIGGER ALL` per user table read from the target catalog
- **AND** the table list is read before target reads close, so rendering in later phases does not query the target

#### Scenario: Suspended constraints are resumed by the runner
- **WHEN** a migration emits `SuspendConstraints(['public.a'])` in the `prepare_target` phase
- **THEN** the runner emits `ResumeConstraints(['public.a'])` in the `finalize_target` phase without a call from the migration

#### Scenario: Index helper removed
- **WHEN** migration code looks for `set_index_commands_for` or `set_indexes_commands` on a `BaseMigration`
- **THEN** neither exists and no index command is generated

### Requirement: Calculated source fields are bound to an adapter
A field computed at the source SHALL be declared as `NativeField(adapter, expression, alias)`, which is also a field strategy, and the source adapter SHALL refuse a `NativeField` of another adapter before sending any query, including the progress count.

#### Scenario: Matching adapter
- **WHEN** a `TableMigration` with `NativeField('postgres', "NULLIF(f_cpf, '')", 'f_cpf')` runs on a `postgres` source
- **THEN** the source query selects `NULLIF(f_cpf, '') AS f_cpf`
- **AND** the value read under `f_cpf` goes to the target column `f_cpf`

#### Scenario: Other adapter
- **WHEN** the same migration runs on a `firebird` source
- **THEN** the migration raises a `ValueError` naming the field and both adapters
- **AND** no query is sent to the source

#### Scenario: Adapter is required
- **WHEN** code creates a `NativeField` without the adapter
- **THEN** the construction raises a `TypeError`

#### Scenario: Dialect-bound expression removed
- **WHEN** migration code imports `SourceSqlExpression` or `check_source_dialect` from `core.strategies`
- **THEN** the import fails

### Requirement: Source identifiers are quoted by the adapter
A relational source adapter SHALL quote the entity and field names of a `SourceQuery` when its database would change or misread them unquoted, reserved words included.

#### Scenario: Reserved word as column name
- **WHEN** a `SourceQuery` asks a `postgres` source for the field `user`
- **THEN** the rendered SQL selects `"user"` and not the session user

#### Scenario: Reserved word on Firebird
- **WHEN** a `SourceQuery` asks a `firebird` source for the field `user`
- **THEN** the rendered SQL selects `"USER"`, the name Firebird stores for an unquoted column

## REMOVED Requirements

### Requirement: Metadata operations are provider-backed
**Reason**: The `MetadataProvider` returned SQL and assumed PostgreSQL catalogs. Trigger handling and sequence resets become operations rendered by the target adapter, reference maps are read through the target adapter `read()`, and the index helper is removed because it either did nothing (index names) or left loaded rows out of the index (table names).
**Migration**: Emit `SuspendConstraints`, `ResumeConstraints` and `ResetIdentity` through the facade shortcuts, and declare reference maps as defined by `padronizar-lookups-de-referencia`.

### Requirement: Direct driver access is transitional only
**Reason**: The fixed connections `pg_v1_conn`, `pg_v2_conn` and `fb_v1_conn` are removed together with the fixed `.env` keys.
**Migration**: Request sources by name with `context.source(name)` and use `NativeQuery` or `Native` operations when a database-specific statement is needed.

### Requirement: Firebird source is optional
**Reason**: With named connections a source exists only when the `.env` declares it, whatever the database. The rule that turned Firebird on by `FB_V1_HOST` has no reason to exist, and the case without Firebird is covered by the scenario "Client without Firebird" of "Base migration exposes adapter-based context".
**Migration**: Declare `SOURCE_<NAME>_ADAPTER=firebird` with its `SOURCE_<NAME>_*` parameters to use a Firebird source, and request it with `context.source('<name>')`.

### Requirement: Source SQL expressions declare their dialect
**Reason**: Field strategies no longer inject SQL into a query the client writes. The source adapter builds the query from a `SourceQuery`, and a computed field is a `NativeField` bound to an adapter, which covers non-SQL sources too.
**Migration**: Replace `SourceSqlExpression(expression, col, dialect='postgres')` with `NativeField('postgres', expression, col)`.
