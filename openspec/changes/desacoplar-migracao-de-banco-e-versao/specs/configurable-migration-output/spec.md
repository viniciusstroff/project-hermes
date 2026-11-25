## MODIFIED Requirements

### Requirement: Runtime writes through output adapter
The migration runtime SHALL deliver the operations emitted by migrations to an `OutputAdapter` through `write(operation)`, phase by phase, and SHALL NOT pass rendered commands or SQL text to the output.

#### Scenario: Runner delivers phase operations
- **WHEN** the standard migration runner opens a phase and a migration emits operations in it
- **THEN** the configured output receives the phase opening and each operation in order

#### Scenario: Context exposes configured output
- **WHEN** a migration context is created
- **THEN** the context exposes the configured output adapter as the only output API

### Requirement: Manifest records output description
Each output adapter SHALL describe itself through `describe()` as a JSON-serializable dictionary, and the run manifest SHALL store that description under the `output` key, including the artifact format of file outputs.

#### Scenario: Default manifest records file output
- **WHEN** a migration runs with the default context and a PostgreSQL target
- **THEN** `manifest.json` contains `"output": {"type": "file", "format": "sql"}`

#### Scenario: Mirror description lists its outputs
- **WHEN** a `MirrorOutput` wraps two file outputs for a MongoDB target
- **THEN** its description is `{"type": "mirror", "outputs": [{"type": "file", "format": "jsonl"}, {"type": "file", "format": "jsonl"}]}`

### Requirement: Mirror output forwards to every output
The `MirrorOutput` SHALL forward phase opening, operations, flush, finish and close to each configured output in the order the outputs were given.

#### Scenario: Mirror forwards calls in order
- **WHEN** a `MirrorOutput` built with two outputs receives `open_phase`, `write`, `flush`, `finish(True)` and `close`
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

### Requirement: Non-file outputs remain out of scope
The default runtime SHALL NOT execute the generated commands against the target database as part of the generation.

#### Scenario: Default migration runs
- **WHEN** a migration runs with the default context
- **THEN** the commands are written to the phase artifacts and are not applied directly to the target database by the output layer

## ADDED Requirements

### Requirement: File output writes artifacts in the target format
The `FileOutput` SHALL render each operation through the target adapter and write the resulting commands to `comandos/<NN>_<phase>.<extension>`, where the extension comes from the artifact format of the target adapter, and the manifest SHALL list each artifact with its phase, path and format.

#### Scenario: PostgreSQL target
- **WHEN** a run with a PostgreSQL target writes the `load_transformed_data` phase
- **THEN** the commands go to `comandos/03_load_transformed_data.sql`

#### Scenario: MongoDB target
- **WHEN** a run with a MongoDB target writes the `load_transformed_data` phase
- **THEN** the commands go to `comandos/03_load_transformed_data.jsonl`, one Extended JSON document per line

#### Scenario: Operation without commands
- **WHEN** the target adapter renders an operation into no command
- **THEN** the file output writes nothing for it

#### Scenario: Value the target cannot store
- **WHEN** the target adapter cannot render a value of an `Insert`
- **THEN** `write()` raises an error naming the entity, the column and the Python type
- **AND** the run fails with that error

### Requirement: Outputs include files of a native copy
The `OutputAdapter` contract SHALL include `include_file(path)`, which appends to the current phase a file of commands written outside the output in the target format, and the `MirrorOutput` SHALL forward it to every output.

#### Scenario: Native copy in the file output
- **WHEN** the `FileOutput` receives `include_file(path)` after operations of the same phase
- **THEN** the artifact of that phase holds the commands of the operations followed by the content of the file

### Requirement: File output keeps artifacts on finish
The `FileOutput` SHALL flush pending commands on `finish()` for both results and SHALL NOT remove or truncate artifacts when the run fails.

#### Scenario: Failed run keeps partial artifacts
- **WHEN** a run fails after writing commands to the `load_transformed_data` phase
- **THEN** the artifact of that phase keeps the commands written before the failure

## REMOVED Requirements

### Requirement: SQL file output preserves current artifacts
**Reason**: Artifacts follow the format of the target adapter and move from `sql/` to `comandos/`. A `.sql` file only exists for SQL targets.
**Migration**: Read artifact paths from the `artifacts` list of the manifest instead of assuming `sql/<phase>.sql`.

### Requirement: BaseMigration writing helpers delegate to output
**Reason**: `write_sql`, `print_comment` and `sql_value` wrote SQL text, which only fits SQL targets.
**Migration**: Emit operations with `self.emit(...)` and the facade shortcuts, and use `Native` operations for database-specific commands.

### Requirement: SQL file output keeps artifacts on finish
**Reason**: Replaced by "File output keeps artifacts on finish", which covers every artifact format.
**Migration**: No client change. The behavior is the same for SQL artifacts.

### Requirement: Base migration dump does not depend on file output
**Reason**: `pg_dump` leaves the core path. Compatible entities are copied through the adapters, and `pg_dump` becomes an optional native copy of the PostgreSQL source adapter.
**Migration**: Replace `dump_ignoring_tables()` and `dump_non_existing_tables()` with `compatible_entities()`.
