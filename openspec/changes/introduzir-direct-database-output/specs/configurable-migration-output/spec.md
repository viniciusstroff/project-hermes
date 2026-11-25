## REMOVED Requirements

### Requirement: Non-file outputs remain out of scope
**Reason**: The `DirectDatabaseOutput` executes generated SQL on the target during generation. The default output keeps writing files only, as stated by the new requirement "Output is selected explicitly".
**Migration**: No action for existing runs. `Migracao.py` without `--output` keeps using `sql-file`. Direct execution needs `--output direct-db` or `--output hybrid` together with `--apply` or `--dry-run`.

## ADDED Requirements

### Requirement: Output is selected explicitly
The default migration context SHALL use `SqlFileOutput` unless the output is chosen on the command line. `Migracao.py` SHALL accept `--output sql-file|direct-db|hybrid`, SHALL require `--apply` or `--dry-run` with `direct-db` and `hybrid`, and SHALL NOT read the output choice from the client `.env`.

#### Scenario: Default output
- **WHEN** `Migracao.py` runs without `--output`
- **THEN** the context uses `SqlFileOutput`
- **AND** no generated statement is executed on the target by the output layer

#### Scenario: Files then application
- **WHEN** `Migracao.py` runs with `--output sql-file --dry-run`
- **THEN** generation writes the SQL artifacts
- **AND** the `SqlArtifactApplier` applies them in `dry-run` mode after generation, as before

#### Scenario: Direct output with mode
- **WHEN** `Migracao.py` runs with `--output direct-db --dry-run`
- **THEN** the context uses a `DirectDatabaseOutput` in `dry-run` mode
- **AND** no application runs after generation

#### Scenario: Hybrid output
- **WHEN** `Migracao.py` runs with `--output hybrid --apply`
- **THEN** the context uses a `MirrorOutput` of a `SqlFileOutput` followed by a `DirectDatabaseOutput` in `apply` mode

#### Scenario: Direct output without mode
- **WHEN** `Migracao.py` runs with `--output direct-db` and neither `--apply` nor `--dry-run`
- **THEN** it exits with an argument error before connecting to any database

#### Scenario: Client constructors stay unchanged
- **WHEN** a client class calls `super().__init__(_clientdir)` without output arguments
- **THEN** the context built for it uses the output chosen on the command line

### Requirement: Output receives files produced outside it
The `OutputAdapter` contract SHALL include `include_file(phase, path)`, which tells the output that the content of a phase was written to `path` outside the output. `SqlFileOutput` SHALL keep that file unchanged and `MirrorOutput` SHALL forward the call to each output in order.

#### Scenario: SQL file output keeps the dump
- **WHEN** a `SqlFileOutput` receives `include_file('dump_compatible_tables', path)` for the file written by `pg_dump`
- **THEN** the file content is unchanged

#### Scenario: Mirror forwards included files
- **WHEN** a `MirrorOutput` with two outputs receives `include_file(phase, path)`
- **THEN** each output receives the same call, the first one before the second

### Requirement: Runner delivers the dump artifact to the output
When the run produced `02_dump_compatible_tables.sql`, the migration runner SHALL call `include_file('dump_compatible_tables', path)` on the configured output after the `prepare_target` hook and before opening the `load_transformed_data` phase. `BaseMigration.dump_tables()` SHALL record the produced path in the migration context.

#### Scenario: Full run
- **WHEN** a non-debug run calls `dump_tables()` and then runs
- **THEN** the output receives `include_file` with the dump path after `run_initial()` and before `open_phase('load_transformed_data')`

#### Scenario: Debug run
- **WHEN** a debug run skips `pg_dump`
- **THEN** the runner does not call `include_file`
