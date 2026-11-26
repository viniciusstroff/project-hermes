## REMOVED Requirements

### Requirement: Non-file outputs remain out of scope
**Reason**: The `DirectDatabaseOutput` runs the commands of the operations on the target during generation. The default output keeps writing files only, as stated by the new requirement "Output is selected explicitly".
**Migration**: No action for existing runs. `Migracao.py` without `--output` keeps using `file`. Direct execution needs `--output direct` or `--output hybrid` together with `--apply` or `--dry-run`.

## ADDED Requirements

### Requirement: Output is selected explicitly
The default migration context SHALL use `FileOutput` unless the output is chosen on the command line. `Migracao.py` SHALL accept `--output file|direct|hybrid`, SHALL require `--apply` or `--dry-run` with `direct` and `hybrid`, and SHALL NOT read the output choice from the client `.env`.

#### Scenario: Default output
- **WHEN** `Migracao.py` runs without `--output`
- **THEN** the context uses `FileOutput`
- **AND** no command is run on the target by the output layer

#### Scenario: Files then application
- **WHEN** `Migracao.py` runs with `--output file --dry-run`
- **THEN** generation writes the artifacts
- **AND** the `ArtifactApplier` applies them in `dry-run` mode after generation, as before

#### Scenario: Direct output with mode
- **WHEN** `Migracao.py` runs with `--output direct --dry-run`
- **THEN** the context uses a `DirectDatabaseOutput` in `dry-run` mode
- **AND** no application runs after generation

#### Scenario: Hybrid output
- **WHEN** `Migracao.py` runs with `--output hybrid --apply`
- **THEN** the context uses a `MirrorOutput` of a `FileOutput` followed by a `DirectDatabaseOutput` in `apply` mode

#### Scenario: Direct output without mode
- **WHEN** `Migracao.py` runs with `--output direct` and neither `--apply` nor `--dry-run`
- **THEN** it exits with an argument error before connecting to any database

#### Scenario: Client constructors stay unchanged
- **WHEN** a client class calls `super().__init__(_clientdir)` without output arguments
- **THEN** the context built for it uses the output chosen on the command line
