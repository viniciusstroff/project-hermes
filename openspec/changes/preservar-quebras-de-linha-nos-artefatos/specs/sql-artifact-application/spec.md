## ADDED Requirements

### Requirement: Applier reads artifacts without translating line breaks
The `ArtifactApplier` and `split_sql_file()` SHALL read artifacts without translating line breaks. A carriage return inside a literal SHALL reach the target as it is in the artifact, and a `--` comment SHALL end at `\n` or `\r`, as in the PostgreSQL lexer.

#### Scenario: CRLF inside a literal
- **WHEN** an artifact holds `INSERT INTO notes VALUES ('linha 1\r\nlinha 2');`
- **THEN** the command executed on the target holds `\r\n` inside the literal

#### Scenario: Lone carriage return inside a literal
- **WHEN** an artifact holds a literal with a lone `\r`
- **THEN** the command executed on the target holds the lone `\r`

#### Scenario: Comment ended by a carriage return
- **WHEN** an artifact holds `-- comentário` followed by `\r` and a command on the same physical line
- **THEN** the splitter ends the comment at the `\r` and reads the command, as the PostgreSQL server does
