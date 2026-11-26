## MODIFIED Requirements

### Requirement: Each application writes its own report
Each application SHALL write `aplicacoes/<YYYYmmdd-HHMMSS>-<apply|dry-run>/relatorio.json` and `erros.txt` inside the run directory, without changing `manifest.json` or the artifacts in `comandos/`. The `relatorio.json` SHALL record in `source` where the commands came from: `artifacts` for the `ArtifactApplier` and `generation` for the direct output.

#### Scenario: Dry run followed by apply
- **WHEN** a run directory receives a dry run and later an apply
- **THEN** each application has its own folder named with its timestamp and mode
- **AND** `manifest.json` and the files in `comandos/` are unchanged

#### Scenario: Report omits password
- **WHEN** the report records the target connection
- **THEN** it includes host, port, database and user
- **AND** it does not include the password

#### Scenario: Source of the commands
- **WHEN** the `ArtifactApplier` writes its report
- **THEN** `relatorio.json` has `"source": "artifacts"`
