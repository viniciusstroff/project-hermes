## ADDED Requirements

### Requirement: Outputs keep line breaks byte for byte
The `FileOutput` SHALL write each artifact without translating line breaks, and the `include_file(path)` of every output SHALL read the included file without translating line breaks, so a carriage return inside a value reaches the artifact and the target unchanged.

#### Scenario: Text value with CRLF
- **WHEN** the `FileOutput` writes an `Insert` whose text value is `'linha 1\r\nlinha 2'`
- **THEN** the artifact holds the bytes `\r\n` inside the literal

#### Scenario: Native copy with a carriage return
- **WHEN** the `FileOutput` includes a `pg_dump` file whose literal holds a lone `\r`
- **THEN** the artifact of the phase holds the same bytes as the included file
