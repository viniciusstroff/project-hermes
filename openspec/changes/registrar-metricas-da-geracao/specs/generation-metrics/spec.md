## ADDED Requirements

### Requirement: Migration units record their metrics
Each `TableMigration`, `ExpandMigration` and `MultiTargetMigration` run SHALL record a generation unit with its label, source name, number of rows read from the source, number of statements generated per target table, start time, end time, duration and final state.

#### Scenario: Table migration finishes
- **WHEN** a `TableMigration` for `public.students` reads 1500 source rows and writes one insert per row
- **THEN** the unit records 1500 rows read, 1500 statements for `public.students`, its duration and the state `done`

#### Scenario: Expand migration counts source rows
- **WHEN** an `ExpandMigration` reads 10 source rows and expands each one into 3 target rows
- **THEN** the unit records 10 rows read and 30 statements for its target table

#### Scenario: Multi-target migration with a conditional target
- **WHEN** a `MultiTargetMigration` reads 100 source rows and its conditional target accepts 40 of them
- **THEN** the unit records 100 rows read, 100 statements for the main target and 40 statements for the conditional target

#### Scenario: Run fails inside a unit
- **WHEN** an exception interrupts a unit after 250 source rows
- **THEN** the unit records 250 rows read with the state `interrupted`

### Requirement: Phases record their duration and totals
The generation SHALL record each phase opened by the runner, in opening order, with its start time, end time, duration and the totals of the units that ran inside it. The `pg_dump` step SHALL be recorded as the `dump_compatible_tables` phase with its duration, exit code and the size of the generated file.

#### Scenario: Units belong to the open phase
- **WHEN** two units run while `load_transformed_data` is the open phase
- **THEN** that phase reports the sum of their rows read and statements

#### Scenario: Dump phase in a full run
- **WHEN** a run without debug mode executes `pg_dump`
- **THEN** the `dump_compatible_tables` phase records its duration, the `pg_dump` exit code and the size of `02_dump_compatible_tables.sql`

#### Scenario: Debug run without dump
- **WHEN** a run in debug mode skips `pg_dump`
- **THEN** no `dump_compatible_tables` phase is recorded

### Requirement: Strategies report data loss through their value
A field strategy SHALL report a lost source value by returning a `LostValue` from `resolve(row)`, carrying the value to write, a loss reason and a sample of the original value. The default `resolve(row)` SHALL return `raw_value(row)`, and `raw_value(row)` SHALL keep returning the plain value.

#### Scenario: Lookup key missing from the map
- **WHEN** a `Lookup` receives the non-null source value `'X'` and the normalized key is not in its map
- **THEN** `resolve` returns a `LostValue` with the fallback as value, the reason `lookup_miss` and the sample `'X'`
- **AND** the loss names the reference map when the map is a loaded reference map

#### Scenario: Invalid date
- **WHEN** a `DateConvert` receives the non-empty text `'31/02/2001'` that does not match its format
- **THEN** `resolve` returns a `LostValue` with value `None`, the reason `invalid_date` and the sample `'31/02/2001'`

#### Scenario: Text without e-mail
- **WHEN** an `EmailExtract` receives a non-empty text without an e-mail address
- **THEN** `resolve` returns a `LostValue` with value `None` and the reason `email_not_found`

#### Scenario: Empty source value is not a loss
- **WHEN** a `Lookup`, `DateConvert` or `EmailExtract` receives a null or empty source value
- **THEN** `resolve` returns the plain value and no loss is recorded

#### Scenario: Strategy without resolve
- **WHEN** a migration field is a client strategy that does not define `resolve`
- **THEN** the migration uses its `raw_value` and records no loss for it

### Requirement: Losses are grouped with bounded samples
The generation SHALL group losses by unit, target table, column, strategy and reason, keeping the total count and at most 20 distinct sample values per group, each truncated to 100 characters.

#### Scenario: Many losses with the same reason
- **WHEN** a unit records 5000 `lookup_miss` losses for the same column with 300 distinct keys
- **THEN** the group reports a count of 5000 and exactly 20 distinct samples

#### Scenario: Loss value is still written
- **WHEN** a strategy returns a `LostValue`
- **THEN** the generated statement contains the serialized `LostValue` value, as it would without metrics

### Requirement: Generation writes its own report
Each run SHALL write `relatorio-geracao.json` at the root of its run directory, with the run id, status, debug limit, timestamps, phases, units, grouped losses and the size of each SQL artifact when the output writes files, both when the run succeeds and when it fails. The report SHALL NOT change `manifest.json`, the SQL artifacts or the application reports in `aplicacoes/`.

#### Scenario: Successful run
- **WHEN** a run finishes every phase
- **THEN** `relatorio-geracao.json` is written with status `success` before the manifest records `success`

#### Scenario: Failed run
- **WHEN** a hook raises an exception during the run
- **THEN** `relatorio-geracao.json` is written with status `failed`, the phases opened so far and the interrupted unit

#### Scenario: Report kept apart from applications
- **WHEN** a run directory receives the generation report and later a dry run
- **THEN** the generation report stays at the run root and the dry run writes its report under `aplicacoes/`
- **AND** `manifest.json` has the same keys as before this change

### Requirement: Generation emits a structured log
The migration logger SHALL write `logs/migration.jsonl` next to `logs/migration.log`, with one JSON object per event holding the timestamp, run id, level, current phase, current unit, event name, message and event data. The text log SHALL keep its current line format.

#### Scenario: Log line becomes an event
- **WHEN** migration code calls `print_log('Inserindo alunos')` while `load_transformed_data` is open
- **THEN** `migration.log` receives the line in the current text format
- **AND** `migration.jsonl` receives an event with level `info`, phase `load_transformed_data` and the same message

#### Scenario: Error level
- **WHEN** the runner logs the exception that failed the run
- **THEN** the JSON event has level `error`

#### Scenario: Unit and loss events
- **WHEN** a unit finishes with recorded losses
- **THEN** `migration.jsonl` receives a `unit_finished` event with the unit metrics and one `data_loss` event per loss group

### Requirement: Text log ends with a readable summary
Before writing the generation report, the runner SHALL log one line per unit with rows read, statements and duration, and one warning line per loss group with its count and up to five samples.

#### Scenario: Summary with a loss
- **WHEN** the run recorded 12 `lookup_miss` losses for `public.students.f_status` from the map `student_statuses`
- **THEN** the text log has a warning line naming the column, the count, the map and up to five sample keys
