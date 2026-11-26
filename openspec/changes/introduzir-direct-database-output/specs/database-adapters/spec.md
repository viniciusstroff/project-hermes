## ADDED Requirements

### Requirement: Target sessions read commands from lines
The `commands()` method of a target session SHALL read commands from any iterable of lines, in the format of the adapter, numbering them from a given first line, so that the same rules apply to an artifact file and to text rendered in memory.

#### Scenario: Applier reads a file
- **WHEN** the `ArtifactApplier` passes the open artifact file to `commands()`
- **THEN** the commands and their line numbers are the same as before

#### Scenario: Rendered text starts in the middle of a phase
- **WHEN** a caller passes the text of one operation with first line 120
- **THEN** the first command of that text has line 120

#### Scenario: PostgreSQL setting changed by an earlier call
- **WHEN** one call to `commands()` reads `SET standard_conforming_strings = off;` and a later call on the same session reads a string with a backslash
- **THEN** the later call reads the string with the setting off
- **AND** after `end_artifact()` the session reads with the value of the server again

### Requirement: Target sessions can wait between commands
The `session()` method of a target adapter SHALL accept `waits_between_commands`, false by default. When it is true, the session SHALL keep its transaction open while the caller takes time between commands, turning off the idle limits of the adapter that would end it.

#### Scenario: PostgreSQL session for the direct output
- **WHEN** a PostgreSQL session is opened with `waits_between_commands=True`
- **THEN** it sets `idle_in_transaction_session_timeout` to zero when the transaction begins
- **AND** it sets it again after each `RESET ALL`

#### Scenario: Session for the applier
- **WHEN** a session is opened without `waits_between_commands`
- **THEN** it changes no idle limit

#### Scenario: MongoDB session
- **WHEN** a MongoDB session is opened with `waits_between_commands=True`
- **THEN** it behaves as without the option, because the transaction limit of the server is not an idle limit
