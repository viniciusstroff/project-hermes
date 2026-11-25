## ADDED Requirements

### Requirement: Direct output executes statements during generation
The `DirectDatabaseOutput` SHALL split the text it receives into statements following the same lexical rules as the artifact splitter and SHALL execute each complete statement on its write connection before the `write()` call that completed it returns, inside a single transaction for the whole run.

#### Scenario: Complete statement is executed before write returns
- **WHEN** the runner writes `INSERT INTO public.demo (f_id) VALUES (1);\n` in the `load_transformed_data` phase
- **THEN** the statement is executed on the write connection before `write()` returns

#### Scenario: Statement split across writes
- **WHEN** a statement arrives in two `write()` calls and the first one ends inside a string literal
- **THEN** nothing is executed after the first call
- **AND** the complete statement is executed after the second call

#### Scenario: Several statements in one write
- **WHEN** one `write()` call carries several statements, such as the trigger disable commands
- **THEN** each statement is executed separately, in order

#### Scenario: Statement without semicolon at the end of a phase
- **WHEN** a phase ends with text after the last semicolon and the next phase is opened
- **THEN** that text is executed as the last statement of the phase
- **AND** `RESET ALL` is executed before the first statement of the next phase

#### Scenario: Line numbers follow the phase file
- **WHEN** a statement fails in the `load_transformed_data` phase
- **THEN** the report records the line where the statement starts, counted from the start of the phase as in `03_load_transformed_data.sql`

### Requirement: Nothing reaches the target before target reads close
The `DirectDatabaseOutput` SHALL keep the statements delivered to the `setup` phase in memory and SHALL execute them only when the `prepare_target` phase is opened, after the migration context closed target reads.

#### Scenario: Setup statements wait
- **WHEN** statements are written to the `setup` phase while the context is built and the reference maps are loaded
- **THEN** the write connection executes none of them
- **AND** they are executed first, in order, when the `prepare_target` phase is opened

#### Scenario: Reference maps load before any write
- **WHEN** the runner loads the declared reference maps
- **THEN** the write connection has not executed any statement of the run

### Requirement: Mode decides how the transaction ends
In `apply` mode the direct output SHALL end the transaction with `COMMIT` on `finish(True)`. In `dry-run` mode it SHALL end the transaction with `ROLLBACK` on `finish(True)`. In both modes it SHALL roll back on `finish(False)` and on `close()` without a previous `finish(True)`.

#### Scenario: Apply run succeeds
- **WHEN** an `apply` run calls `finish(True)` after every statement ran under its error policy
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
- **WHEN** the final `COMMIT` raises, for example on a deferred constraint
- **THEN** `finish(True)` raises
- **AND** the run manifest status is `failed`

### Requirement: Error policies match the artifact applier
The direct output SHALL support the `stop` and `continue` error policies with the same behavior and defaults as the `SqlArtifactApplier`: `stop` for `apply` and `continue` for `dry-run`.

#### Scenario: Stop policy
- **WHEN** a statement fails under the `stop` policy
- **THEN** the `write()` call that completed the statement raises an error naming the phase file, the statement number and the starting line
- **AND** the run ends with manifest status `failed` and the transaction rolled back

#### Scenario: Continue policy
- **WHEN** a statement fails under the `continue` policy
- **THEN** only that statement is rolled back and recorded in the report
- **AND** generation continues with the next statements

### Requirement: Direct output skips the same statements as the applier
The direct output SHALL skip and count transaction control statements and psql meta-commands, SHALL skip and count `setval` calls in `dry-run` mode, and SHALL fail on `COPY ... FROM stdin`, using the same rules as the `SqlArtifactApplier`.

#### Scenario: Transaction control in generated SQL
- **WHEN** a client writes `COMMIT;` in a phase
- **THEN** the statement is not executed and is counted as skipped transaction control

#### Scenario: Dry run meets setval
- **WHEN** a `dry-run` direct output receives a statement that calls `setval`
- **THEN** the statement is not executed and is counted as a skipped sequence change

### Requirement: Dump artifact is executed in phase order
When the runner includes the dump artifact, the direct output SHALL execute its statements after the `prepare_target` statements and before the first `load_transformed_data` statement, reading the file in streaming, and SHALL execute `RESET ALL` after it.

#### Scenario: Full run with dump
- **WHEN** a non-debug run includes `02_dump_compatible_tables.sql`
- **THEN** the dump statements run after the last `prepare_target` statement and before the first `load_transformed_data` statement
- **AND** the `BEGIN` and `COMMIT` that `pg_dump` writes around large objects are skipped

#### Scenario: Dump changes search_path
- **WHEN** the dump runs `SELECT pg_catalog.set_config('search_path', '', false);`
- **THEN** `RESET ALL` runs before the first `load_transformed_data` statement

### Requirement: Direct output has the same effect as applying the artifacts
For the same generated statements and the same mode, the direct output SHALL execute the same statements, in the same order and with the same skipped statements, as the `SqlArtifactApplier` executes over the artifacts of a `sql-file` run.

#### Scenario: Same statements as the applier
- **WHEN** a run is generated with the `hybrid` output in `dry-run` mode and its artifacts are then applied by the `SqlArtifactApplier` in `dry-run` mode
- **THEN** both executed the same list of statements in the same order
- **AND** both reports count the same skipped statements per reason

### Requirement: Direct output writes a report and describes itself
The direct output SHALL write its report to `aplicacoes/<YYYYmmdd-HHMMSS>-<mode>/` inside the run directory, with `source` set to `generation` and without the password, and its `describe()` SHALL include the output type, the mode, the error policy and the report directory.

#### Scenario: Manifest records a validation
- **WHEN** a run uses the direct output in `dry-run` mode with the `continue` policy
- **THEN** the manifest `output` entry contains `"type": "direct-db"`, `"mode": "dry-run"`, `"on_error": "continue"` and the report directory

#### Scenario: Report omits password
- **WHEN** the direct output records the target connection in `relatorio.json`
- **THEN** it includes host, port, database and user
- **AND** it does not include the password

### Requirement: Write connection does not wait for read locks
The direct output SHALL use its own connection to the target, opened from the `PG_V2_*` keys with UTF-8 client encoding and with `idle_in_transaction_session_timeout` disabled for the session. The direct output SHALL execute statements only after target reads are closed, which ends the read transaction of the metadata provider connection, so the write connection does not wait for locks taken by reference map and trigger catalog queries.

#### Scenario: Prepare phase truncates a table read by a reference map
- **WHEN** a reference map was loaded from a table that `prepare_target` truncates
- **THEN** the `TRUNCATE` on the write connection does not wait for a lock held by the provider connection

#### Scenario: Session settings survive phase resets
- **WHEN** the direct output runs `RESET ALL` between phases
- **THEN** `idle_in_transaction_session_timeout` stays disabled on the write connection
