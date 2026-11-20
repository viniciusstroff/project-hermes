# configurable-migration-output Specification

## Purpose
Define how the migration runtime delivers generated SQL through an `OutputAdapter`, keeping `SqlFileOutput` as the default implementation and the current SQL artifacts unchanged.
## Requirements
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

### Requirement: Runner reports execution result to output
The migration runner SHALL call `finish(success)` on the configured output adapter exactly once per run, after the last phase write on success and before the manifest records the final status.

#### Scenario: Successful run finishes output
- **WHEN** a migration run completes every phase without errors
- **THEN** the runner calls `output.flush()` and then `output.finish(True)` once
- **AND** the manifest status is written as `success` after `finish(True)` returns

#### Scenario: Failed run finishes output with failure
- **WHEN** a migration hook raises an exception during the run
- **THEN** the runner calls `output.finish(False)` once
- **AND** the manifest status is written as `failed` with the exception message

#### Scenario: Output fails to confirm a successful run
- **WHEN** `output.finish(True)` raises an exception
- **THEN** the manifest status is written as `failed`
- **AND** the runner does not call `output.finish(False)` afterwards

#### Scenario: Output fails while finishing a failed run
- **WHEN** a migration hook raises an exception and `output.finish(False)` also raises
- **THEN** the finish error is logged
- **AND** the manifest status is still written as `failed` with the original exception message

### Requirement: SQL file output keeps artifacts on finish
The `SqlFileOutput` SHALL flush pending SQL on `finish()` for both results and SHALL NOT remove or truncate artifacts when the run fails.

#### Scenario: Failed run keeps partial artifacts
- **WHEN** a run fails after writing SQL to the `load_transformed_data` phase
- **THEN** `03_load_transformed_data.sql` keeps the SQL written before the failure

### Requirement: Mirror output forwards to every output
The `MirrorOutput` SHALL forward phase opening, writes, comments, flush, finish and close to each configured output in the order the outputs were given.

#### Scenario: Mirror forwards calls in order
- **WHEN** a `MirrorOutput` built with two outputs receives `open_phase`, `write`, `comment`, `flush`, `finish(True)` and `close`
- **THEN** each output receives the same calls with the same arguments
- **AND** the first output receives each call before the second output

#### Scenario: Mirror stops writes on first error
- **WHEN** the first output raises during `write`
- **THEN** the error propagates
- **AND** the second output does not receive that write

#### Scenario: Mirror finishes and closes every output when one fails
- **WHEN** the first output raises during `finish` or `close`
- **THEN** the second output still receives the same `finish` or `close` call
- **AND** the first error is raised after every output was called

#### Scenario: Mirror requires at least one output
- **WHEN** a `MirrorOutput` is created without outputs
- **THEN** it raises `ValueError`

### Requirement: Manifest records output description
Each output adapter SHALL describe itself through `describe()` as a JSON-serializable dictionary, and the run manifest SHALL store that description under the `output` key.

#### Scenario: Default manifest records SQL file output
- **WHEN** a migration runs with the default context
- **THEN** `manifest.json` contains `"output": {"type": "sql-file"}`

#### Scenario: Mirror description lists its outputs
- **WHEN** a `MirrorOutput` wraps two `SqlFileOutput` instances
- **THEN** its description is `{"type": "mirror", "outputs": [{"type": "sql-file"}, {"type": "sql-file"}]}`

### Requirement: Base migration dump does not depend on file output
`BaseMigration.dump_tables()` SHALL NOT require the configured output adapter to expose a `file` attribute.

#### Scenario: Dump runs with mirror output
- **WHEN** `dump_tables()` runs with a `MirrorOutput` as the configured output
- **THEN** the `pg_dump` command runs without accessing an output file handle

