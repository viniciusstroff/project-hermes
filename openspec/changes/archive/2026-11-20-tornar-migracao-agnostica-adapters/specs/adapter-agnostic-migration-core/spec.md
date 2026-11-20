## ADDED Requirements

### Requirement: Core consumes source adapters
The migration core SHALL read source data through a `SourceAdapter` contract instead of calling concrete PostgreSQL or Firebird connection and cursor APIs directly.

#### Scenario: Engine reads PostgreSQL source through adapter
- **WHEN** a table migration runs against the default PostgreSQL V1 source
- **THEN** the engine obtains rows through the configured source adapter without referencing `psycopg2` cursor APIs in the engine

#### Scenario: Engine reads Firebird source through adapter
- **WHEN** a table migration runs against the default Firebird V1 source
- **THEN** the engine obtains dictionary-like rows through the configured source adapter without branching on a Firebird source flag in the engine

### Requirement: Source adapters own source-specific query behavior
The source adapter or its dialect SHALL own source-specific query behavior, including pagination or debug limit syntax.

#### Scenario: PostgreSQL source applies debug limit
- **WHEN** debug mode sets a limit of 100 for a PostgreSQL source query
- **THEN** the rendered source SQL uses PostgreSQL-compatible limit syntax

#### Scenario: Firebird source applies debug limit
- **WHEN** debug mode sets a limit of 100 for a Firebird source query
- **THEN** the rendered source SQL uses Firebird-compatible row limiting syntax

### Requirement: Core consumes target adapters
The migration core SHALL build target insert statements through a `TargetAdapter` contract instead of formatting PostgreSQL `INSERT` statements directly in migration classes.

#### Scenario: Table migration writes target insert
- **WHEN** a `TableMigration` processes a source row
- **THEN** it passes the target table, insert columns, and field values to the configured target adapter to produce the output statement

#### Scenario: Multi-target migration writes target inserts
- **WHEN** a `MultiTargetMigration` processes a source row with multiple eligible targets
- **THEN** each target insert is produced through the configured target adapter

### Requirement: Target dialect owns value serialization
The target adapter or target dialect SHALL own SQL serialization of Python values used in generated output.

#### Scenario: Strategy returns string with quotes
- **WHEN** a field strategy produces a Python string containing a single quote
- **THEN** the generated target statement escapes the value using the configured target dialect

#### Scenario: Strategy returns null value
- **WHEN** a field strategy produces a null Python value
- **THEN** the generated target statement serializes the value using the configured target dialect null representation

### Requirement: Metadata operations are provider-backed
The migration core SHALL perform auxiliary target metadata operations through a `MetadataProvider` contract instead of embedding PostgreSQL catalog queries in `BaseMigration` or Acme code.

#### Scenario: Trigger commands are generated for PostgreSQL target
- **WHEN** the default PostgreSQL target prepares trigger disable and enable commands
- **THEN** the commands are provided by the PostgreSQL metadata provider

#### Scenario: Reference map is loaded from target
- **WHEN** an Acme loads a reference map from the target database
- **THEN** the lookup is executed through the configured metadata provider and cached by the migration context

### Requirement: Base migration exposes adapter-based context
`BaseMigration` SHALL construct and expose adapter-based source, target, dialect, and metadata context while preserving the current default PostgreSQL V1, Firebird V1, and PostgreSQL V2 configuration path.

#### Scenario: Default migration starts with current environment keys
- **WHEN** a migration is created using the existing `.env` keys for PostgreSQL V1, Firebird V1, and PostgreSQL V2
- **THEN** `BaseMigration` creates the corresponding adapters and the migration can run with behavior equivalent to the current flow

#### Scenario: Acme code requests source engine
- **WHEN** an Acme uses the core helper for the primary source or Firebird source
- **THEN** it receives a migration engine backed by the corresponding source adapter and configured target adapter

### Requirement: Direct driver access is transitional only
The core SHALL provide adapter-first helpers for Acmes so new migration code does not need to access `pg_v1_conn`, `pg_v2_conn`, or `fb_v1_conn` directly.

#### Scenario: Acme model uses adapter helpers
- **WHEN** the model Acme performs table migrations, reference lookups, trigger handling, index handling, or sequence resets
- **THEN** it uses core helpers, adapters, or metadata providers instead of direct driver connection attributes

#### Scenario: Legacy connection attributes are still present during transition
- **WHEN** existing Acme code still depends on legacy connection attributes
- **THEN** the default migration context keeps compatible accessors until those Acmes are migrated
