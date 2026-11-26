## ADDED Requirements

### Requirement: Direct output reads included files without translating line breaks
The `DirectDatabaseOutput` SHALL read the file given to `include_file(path)` without translating line breaks, so the commands it runs are the ones the `ArtifactApplier` runs over the same file.

#### Scenario: Native copy with CRLF
- **WHEN** the direct output includes a `pg_dump` file whose literal holds `\r\n`
- **THEN** the command executed on the target holds `\r\n` inside the literal
