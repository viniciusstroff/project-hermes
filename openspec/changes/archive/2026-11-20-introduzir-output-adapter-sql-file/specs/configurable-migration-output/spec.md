## ADDED Requirements

### Requirement: Runtime writes through output adapter
The migration runtime SHALL deliver generated SQL through an `OutputAdapter` contract instead of depending directly on a SQL file writer implementation.

#### Scenario: Runner writes phase output through adapter
- **WHEN** the standard migration runner opens a phase and writes SQL for that phase
- **THEN** it calls the configured output adapter for phase opening, writing, comments, flushing, and closing

#### Scenario: Context exposes configured output
- **WHEN** a migration context is created
- **THEN** the context exposes the configured output adapter as the preferred output API

### Requirement: SQL file output preserves current artifacts
The default output adapter SHALL preserve the current SQL file artifact behavior.

#### Scenario: Default output opens prepare phase
- **WHEN** the default migration context opens the `prepare_target` phase
- **THEN** SQL is written to the existing `01_prepare_target.sql` artifact path

#### Scenario: Default output opens load phase
- **WHEN** the default migration context opens the `load_transformed_data` phase
- **THEN** SQL is written to the existing `03_load_transformed_data.sql` artifact path

#### Scenario: Default output opens finalize phase
- **WHEN** the default migration context opens the `finalize_target` phase
- **THEN** SQL is written to the existing `04_finalize_target.sql` artifact path

### Requirement: BaseMigration writing helpers delegate to output
The `BaseMigration` facade SHALL keep its SQL-writing helpers and delegate them to the configured output adapter, while the context SHALL expose `output` as its only output API.

#### Scenario: Base migration writes SQL through facade helper
- **WHEN** existing Acme code calls `BaseMigration.write_sql(sql)`
- **THEN** the SQL is delegated to the configured output adapter

#### Scenario: Context has no writer alias
- **WHEN** composed code needs to write SQL through the migration context
- **THEN** it uses `context.output`
- **AND** the context does not expose a `writer` attribute

### Requirement: Non-file outputs remain out of scope
The default runtime SHALL NOT execute generated SQL directly against the target database as part of this change.

#### Scenario: Default migration runs
- **WHEN** a migration runs with the default context
- **THEN** generated SQL is written to SQL artifacts and is not applied directly to the target database by the output layer
