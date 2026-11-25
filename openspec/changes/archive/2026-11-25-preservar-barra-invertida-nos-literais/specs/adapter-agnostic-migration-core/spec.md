## ADDED Requirements

### Requirement: PostgreSQL dialect preserves string content
The PostgreSQL target dialect SHALL serialize Python strings so that the value stored in the target is identical to the original string, including backslashes, regardless of the `standard_conforming_strings` setting of the session that applies the generated SQL.

#### Scenario: String with backslash
- **WHEN** a field value is the string `a\b`
- **THEN** the dialect renders `E'a\\b'`
- **AND** the target stores `a\b`

#### Scenario: String ending with backslash
- **WHEN** a field value ends with a backslash
- **THEN** the rendered literal is closed by its final quote
- **AND** the statement that follows it in the artifact is not absorbed by the literal

#### Scenario: Backslash next to a quote
- **WHEN** a field value is `O\'Neil`
- **THEN** the dialect renders `E'O\\''Neil'`
- **AND** the target stores `O\'Neil`

#### Scenario: Text that looks like an escape sequence
- **WHEN** a field value contains `\n`, `\x41` or `\101` as plain text
- **THEN** the target stores those characters literally, without turning them into a line break or into `A`

#### Scenario: Session with standard_conforming_strings off
- **WHEN** the generated SQL is applied in a session with `standard_conforming_strings = off`
- **THEN** the target stores the same value as in a session with the setting on

#### Scenario: String without backslash
- **WHEN** a field value has no backslash
- **THEN** the dialect renders a standard `'...'` literal with doubled single quotes, unchanged from the previous behavior
