# direct-database-output Specification

## Purpose
Define how the direct database output runs the commands of each operation on the target during the generation, through the session of the target adapter, in a single transaction, with the modes, error policies, capability checks and report of the artifact applier.
## Requirements
### Requirement: Direct output runs operations through the target session
The `DirectDatabaseOutput` SHALL render each operation it receives through the target adapter, SHALL read the rendered text into commands with the `commands()` method of a target session and SHALL hand each command to the error policy before the `write()` call returns, inside a single transaction for the whole run. It SHALL NOT split, parse or classify commands itself.

#### Scenario: Insert on a PostgreSQL target
- **WHEN** the runner writes an `Insert` in the `load_transformed_data` phase of a run with a PostgreSQL target
- **THEN** the `INSERT` rendered by the adapter has run on the write session before `write()` returns

#### Scenario: Operation with several commands
- **WHEN** one operation renders several commands, such as a `Native` operation or a `SuspendConstraints` over several tables
- **THEN** each command is handed to the session separately, in order

#### Scenario: Operation without commands
- **WHEN** the target adapter renders an operation into no command, such as a `Comment` on a MongoDB target
- **THEN** the session receives nothing for it

#### Scenario: Session that runs commands in batches
- **WHEN** the MongoDB session defers inserts to run them in a batch and an insert of the batch fails
- **THEN** the failure is reported at the latest on the phase change or on `finish()`
- **AND** the report names the command of the operation that failed

#### Scenario: Line numbers follow the phase file
- **WHEN** a command fails in the `load_transformed_data` phase
- **THEN** the report records the line where the command starts, counted from the start of the phase as in `03_load_transformed_data.<extension>`

### Requirement: Nothing reaches the target before target reads close
The `DirectDatabaseOutput` SHALL keep the operations delivered to the `setup` phase in memory and SHALL open its write session and run them only when the `prepare_target` phase is opened, after the migration context closed target reads.

#### Scenario: Setup operations wait
- **WHEN** operations are written to the `setup` phase while the context is built and the reference maps are loaded
- **THEN** no write session is open and none of them has run
- **AND** they run first, in order, when the `prepare_target` phase is opened

#### Scenario: Reference maps load before any write
- **WHEN** the runner loads the declared reference maps
- **THEN** the direct output has not connected to the target

### Requirement: Direct output uses its own target session
The direct output SHALL connect its own target adapter from the `TARGET_*` keys and SHALL open its session with `waits_between_commands=True`. It SHALL NOT write through the target adapter of the migration context.

#### Scenario: Prepare phase clears an entity read by a reference map
- **WHEN** a reference map was loaded from a PostgreSQL table that `prepare_target` clears
- **THEN** the `TRUNCATE` on the write session does not wait for a lock held by the read connection of the context

### Requirement: Mode decides how the transaction ends
In `apply` mode the direct output SHALL commit the transaction on `finish(True)`. In `dry-run` mode it SHALL roll the transaction back on `finish(True)`. In both modes it SHALL roll back on `finish(False)` and on `close()` without a previous `finish(True)`.

#### Scenario: Apply run succeeds
- **WHEN** an `apply` run calls `finish(True)` after every command ran under its error policy
- **THEN** the transaction is committed and the report result is `committed`

#### Scenario: Dry run succeeds
- **WHEN** a `dry-run` run calls `finish(True)`
- **THEN** the transaction is rolled back and the report result is `rolled_back`

#### Scenario: Run fails
- **WHEN** a migration hook raises and the runner calls `finish(False)`
- **THEN** the transaction is rolled back and the report result is `failed`

#### Scenario: Run is interrupted
- **WHEN** the output is closed without a previous `finish(True)`, as after an interruption
- **THEN** the transaction is rolled back

#### Scenario: Commit fails
- **WHEN** the final commit raises, for example on a deferred constraint
- **THEN** `finish(True)` raises
- **AND** the run manifest status is `failed`

### Requirement: Error policies and capabilities match the artifact applier
The direct output SHALL support the `stop` and `continue` error policies with the same behavior and defaults as the `ArtifactApplier`, using the same policy classes, and SHALL refuse a mode or policy the target cannot honor with the same capability checks: first the capabilities declared by the adapter, before any connection, then the capabilities of the session when it opens.

#### Scenario: Stop policy
- **WHEN** a command fails under the `stop` policy
- **THEN** the `write()` call or the phase change that ran it raises an error naming the phase file, the command number and the starting line
- **AND** the run ends with manifest status `failed` and the transaction rolled back

#### Scenario: Continue policy
- **WHEN** a command fails under the `continue` policy
- **THEN** only that command is rolled back and recorded in the report
- **AND** generation continues with the next operations

#### Scenario: Default policy without savepoints
- **WHEN** a `dry-run` direct output targets MongoDB without `--on-error`
- **THEN** the policy is `stop`

#### Scenario: Target without transactions
- **WHEN** the direct output is built for a target adapter that declares no transactions
- **THEN** it refuses the output in both modes before connecting to any database

#### Scenario: Session knows less than the declaration
- **WHEN** the MongoDB write session opens on a server outside a replica set
- **THEN** the run fails before the first command, naming the missing capability

### Requirement: Direct output skips the same commands as the applier
The commands the direct output runs and skips SHALL come from the same session rules as in the `ArtifactApplier`, counted by reason in the report.

#### Scenario: Transaction control in a native operation
- **WHEN** a client emits `Native('postgres', 'COMMIT;')` in a phase
- **THEN** the command is not executed and is counted as skipped transaction control

#### Scenario: Dry run meets setval
- **WHEN** a `dry-run` direct output receives a `ResetIdentity` rendered as a `setval` call on a PostgreSQL target
- **THEN** the command is not executed and is counted as a skipped sequence change

### Requirement: Native copy runs in phase order
When the runner includes the file of a native copy, the direct output SHALL run its commands through the session after the operations already written to the `copy_compatible_entities` phase, reading the file line by line, with the same rules as the applier, and SHALL end the artifact on the session before the next phase.

#### Scenario: Run with pg_dump
- **WHEN** a run with `SOURCE_V1_NATIVE_COPY=pg_dump` includes the dump file
- **THEN** the dump commands run after the copied entities of the phase and before the first `load_transformed_data` command
- **AND** the `BEGIN` and `COMMIT` that `pg_dump` writes around large objects are skipped

#### Scenario: Dump changes search_path
- **WHEN** the dump runs `SELECT pg_catalog.set_config('search_path', '', false);`
- **THEN** `RESET ALL` runs before the first `load_transformed_data` command

### Requirement: Direct output has the same effect as applying the artifacts
For the same operations and the same mode, the direct output SHALL run the same commands, in the same order and with the same skipped commands, as the `ArtifactApplier` runs over the artifacts of a `file` run.

#### Scenario: Same commands as the applier
- **WHEN** a run is generated with the `hybrid` output in `dry-run` mode and its artifacts are then applied by the `ArtifactApplier` in `dry-run` mode
- **THEN** both ran the same list of commands in the same order
- **AND** both reports count the same skipped commands per reason

### Requirement: Direct output writes a report and describes itself
The direct output SHALL write its report to `aplicacoes/<YYYYmmdd-HHMMSS>-<mode>/` inside the run directory, with `source` set to `generation` and the session description without the password, and its `describe()` SHALL include the output type, the target adapter, the mode, the error policy and the report directory.

#### Scenario: Manifest records a validation
- **WHEN** a run uses the direct output in `dry-run` mode with the `continue` policy on a PostgreSQL target
- **THEN** the manifest `output` entry contains `"type": "direct"`, `"adapter": "postgres"`, `"mode": "dry-run"`, `"on_error": "continue"` and the report directory

#### Scenario: Report omits password
- **WHEN** the direct output records the target connection in `relatorio.json`
- **THEN** it includes the description of the session
- **AND** it does not include the password

#### Scenario: MongoDB transaction limit
- **WHEN** the direct output opens a MongoDB write session
- **THEN** the console shows the `transactionLifetimeLimitSeconds` read by the session
- **AND** the report records it

### Requirement: Direct output reads included files without translating line breaks
The `DirectDatabaseOutput` SHALL read the file given to `include_file(path)` without translating line breaks, so the commands it runs are the ones the `ArtifactApplier` runs over the same file.

#### Scenario: Native copy with CRLF
- **WHEN** the direct output includes a `pg_dump` file whose literal holds `\r\n`
- **THEN** the command executed on the target holds `\r\n` inside the literal

