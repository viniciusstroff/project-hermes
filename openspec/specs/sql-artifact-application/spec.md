# sql-artifact-application Specification

## Purpose
Define how the SQL artifacts of a generated run are applied to the target database in a single transaction, with apply and dry-run modes, stop and continue error policies, PostgreSQL statement splitting and one report per application.
## Requirements
### Requirement: Applier runs artifacts in phase order within one transaction
The `SqlArtifactApplier` SHALL apply the SQL artifacts of a run directory in phase order (`00_setup`, `01_prepare_target`, `02_dump_compatible_tables`, `03_load_transformed_data`, `04_finalize_target`) inside a single database transaction.

#### Scenario: Full run is applied in order
- **WHEN** the applier receives a run directory generated without debug mode
- **THEN** it executes the statements of the five artifacts in phase order on one connection and one transaction

#### Scenario: Debug run has no dump artifact
- **WHEN** the run manifest records debug mode and `02_dump_compatible_tables.sql` does not exist
- **THEN** the applier applies the other four artifacts in phase order

### Requirement: Applier refuses incomplete runs
The applier SHALL refuse a run directory before connecting to the target when the manifest status is not `success` or a required artifact is missing.

#### Scenario: Generation failed
- **WHEN** the run manifest status is `failed` or `running`
- **THEN** the applier stops with an error naming the status
- **AND** no connection to the target is opened

#### Scenario: Dump artifact missing in a full run
- **WHEN** the run is not a debug run and `02_dump_compatible_tables.sql` does not exist
- **THEN** the applier stops with an error naming the missing file

### Requirement: Apply mode commits and dry run rolls back
In `apply` mode the applier SHALL end the transaction with `COMMIT` only when the application completes under its error policy. In `dry-run` mode the applier SHALL always end the transaction with `ROLLBACK`.

#### Scenario: Apply completes
- **WHEN** an `apply` run executes every statement under its error policy
- **THEN** the transaction is committed and the report result is `committed`

#### Scenario: Apply stops on error
- **WHEN** an `apply` run with the `stop` policy hits a failing statement
- **THEN** the transaction is rolled back
- **AND** the report result is `failed` and lists the failing statement

#### Scenario: Dry run completes
- **WHEN** a `dry-run` application finishes
- **THEN** the transaction is rolled back and the report result is `rolled_back`

#### Scenario: Process is interrupted
- **WHEN** the application is interrupted before the final `COMMIT`
- **THEN** the transaction is rolled back

### Requirement: Continue policy records errors and keeps going
With the `continue` policy the applier SHALL roll back only the failing statements, record each failure and keep applying the remaining statements.

#### Scenario: One statement fails inside a batch
- **WHEN** one statement fails inside a batch under the `continue` policy
- **THEN** the other statements of that batch remain applied in the transaction
- **AND** the report records the file, statement number, starting line, SQLSTATE and message of the failing statement

#### Scenario: Default policies
- **WHEN** no error policy is given
- **THEN** `apply` uses `stop` and `dry-run` uses `continue`

### Requirement: Applier controls transaction and session state
The applier SHALL skip transaction control statements and psql meta-commands found in artifacts, SHALL reset session settings after each artifact, and SHALL skip `setval` calls in dry-run mode, counting each skipped statement by reason in the report.

#### Scenario: Dump wraps large objects in a transaction
- **WHEN** an artifact contains `BEGIN;` and `COMMIT;` statements
- **THEN** the applier does not execute them and counts them as skipped transaction control

#### Scenario: Dump contains psql meta-commands
- **WHEN** a statement starts with a backslash meta-command such as `\restrict`
- **THEN** the applier does not send it to the server and counts it as a skipped meta-command

#### Scenario: Dump changes search_path
- **WHEN** `02_dump_compatible_tables.sql` runs `SELECT pg_catalog.set_config('search_path', '', false);`
- **THEN** the applier runs `RESET ALL` before the next artifact
- **AND** unqualified table names in `03_load_transformed_data.sql` resolve with the session default `search_path`

#### Scenario: Dry run meets setval
- **WHEN** a `dry-run` application reaches a statement that calls `setval`
- **THEN** the statement is not executed and is counted as a skipped sequence change

#### Scenario: Artifact uses COPY FROM stdin
- **WHEN** an artifact contains `COPY ... FROM stdin`
- **THEN** the application fails with an error stating that inline COPY data is not supported

### Requirement: Statements are split by PostgreSQL lexical rules
The statement splitter SHALL end a statement only at a semicolon outside string literals, escape string literals, quoted identifiers, dollar-quoted strings and comments.

#### Scenario: Semicolons inside literals
- **WHEN** an artifact contains `INSERT INTO t VALUES ('a;b', E'c\';d', $x$e;f$x$);` followed by another statement
- **THEN** the splitter yields two statements and the first one keeps its literals intact

#### Scenario: Statement without final semicolon
- **WHEN** an artifact ends with a statement without a semicolon
- **THEN** the splitter yields it as the last statement

#### Scenario: Unterminated literal
- **WHEN** an artifact ends inside an unterminated string literal
- **THEN** the application fails with an error naming the file and the line where the literal starts

### Requirement: Each application writes its own report
Each application SHALL write `aplicacoes/<YYYYmmdd-HHMMSS>-<apply|dry-run>/relatorio.json` and `erros.txt` inside the run directory, without changing `manifest.json` or the SQL artifacts.

#### Scenario: Dry run followed by apply
- **WHEN** a run directory receives a dry run and later an apply
- **THEN** each application has its own folder named with its timestamp and mode
- **AND** `manifest.json` and the files in `sql/` are unchanged

#### Scenario: Report omits password
- **WHEN** the report records the target connection
- **THEN** it includes host, port, database and user
- **AND** it does not include the password

