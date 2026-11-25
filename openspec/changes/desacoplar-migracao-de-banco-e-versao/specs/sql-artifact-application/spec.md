## MODIFIED Requirements

### Requirement: Applier runs artifacts in phase order within one transaction
The applier SHALL apply the artifacts listed in the run manifest in phase order, reading each one with the artifact format of the target adapter recorded in the manifest and executing its commands through one session of that adapter, inside a single transaction.

#### Scenario: Full run is applied in order
- **WHEN** the applier receives a run directory generated without debug mode
- **THEN** it executes the commands of every listed artifact in phase order in one session and one transaction

#### Scenario: Debug run with a limited copy
- **WHEN** the run manifest records debug mode
- **THEN** the `copy_compatible_entities` artifact, with at most the debug limit of rows per entity, is applied in phase order like the others

#### Scenario: MongoDB artifacts
- **WHEN** the manifest records a `mongodb` target with `jsonl` artifacts and the target is a replica set
- **THEN** the applier reads one command per line and executes them in one session transaction

### Requirement: Applier controls transaction and session state
When the target adapter is PostgreSQL, the application SHALL skip transaction control statements and psql meta-commands found in artifacts, SHALL reset session settings after each artifact, and SHALL skip `setval` calls in dry-run mode, counting each skipped statement by reason in the report. These rules SHALL live in the PostgreSQL adapter and SHALL NOT apply to other adapters.

#### Scenario: Dump wraps large objects in a transaction
- **WHEN** a PostgreSQL artifact contains `BEGIN;` and `COMMIT;` statements
- **THEN** the applier does not execute them and counts them as skipped transaction control

#### Scenario: Dump contains psql meta-commands
- **WHEN** a PostgreSQL statement starts with a backslash meta-command such as `\restrict`
- **THEN** the applier does not send it to the server and counts it as a skipped meta-command

#### Scenario: Dump changes search_path
- **WHEN** the PostgreSQL copy artifact runs `SELECT pg_catalog.set_config('search_path', '', false);`
- **THEN** the session runs `RESET ALL` before the next artifact

#### Scenario: Dry run meets setval
- **WHEN** a PostgreSQL `dry-run` application reaches a statement that calls `setval`
- **THEN** the statement is not executed and is counted as a skipped sequence change

### Requirement: Continue policy records errors and keeps going
With the `continue` policy the applier SHALL roll back only the failing commands, record each failure and keep applying the remaining commands. The default policy SHALL be `stop` for `apply`, and for `dry-run` it SHALL be `continue` when the target supports savepoints and `stop` otherwise.

#### Scenario: One command fails inside a batch
- **WHEN** one command fails inside a batch under the `continue` policy
- **THEN** the other commands of that batch remain applied in the transaction
- **AND** the report records the file, command number, starting line, error code given by the adapter and message of the failing command

#### Scenario: Default policies
- **WHEN** no error policy is given
- **THEN** `apply` uses `stop`
- **AND** `dry-run` uses `continue` on PostgreSQL and `stop` on MongoDB

### Requirement: Each application writes its own report
Each application SHALL write `aplicacoes/<YYYYmmdd-HHMMSS>-<apply|dry-run>/relatorio.json` and `erros.txt` inside the run directory, without changing `manifest.json` or the artifacts in `comandos/`.

#### Scenario: Dry run followed by apply
- **WHEN** a run directory receives a dry run and later an apply
- **THEN** each application has its own folder named with its timestamp and mode
- **AND** `manifest.json` and the files in `comandos/` are unchanged

#### Scenario: Report omits password
- **WHEN** the report records the target connection
- **THEN** it includes host, port, database and user
- **AND** it does not include the password

## ADDED Requirements

### Requirement: Applier refuses modes the target cannot honor
The applier SHALL check the capabilities of the target adapter before connecting for the application and SHALL refuse a mode or policy the target cannot honor, naming the missing capability.

#### Scenario: Dry run without transactions
- **WHEN** a dry run is requested for a MongoDB target that is a standalone server
- **THEN** the application is refused
- **AND** the error states that the target does not support transactions

#### Scenario: Continue policy without savepoints
- **WHEN** `--on-error continue` is requested for a MongoDB target
- **THEN** the application is refused
- **AND** the error states that the target does not support savepoints

### Requirement: Target connection comes from the target keys
`Aplicar.py` SHALL take the target adapter from the run manifest and its connection parameters from `TARGET_*` environment variables, with the same names used in the client `.env`, and SHALL NOT read libpq variables.

#### Scenario: Apply a MongoDB run
- **WHEN** `Aplicar.py` runs on a run directory whose manifest records a `mongodb` target, with `TARGET_URI` set
- **THEN** it connects through the MongoDB adapter and shows the target and mode before starting

#### Scenario: Target keys missing
- **WHEN** the parameters required by the target adapter are not set
- **THEN** `Aplicar.py` exits with code 2 naming the missing `TARGET_*` keys
