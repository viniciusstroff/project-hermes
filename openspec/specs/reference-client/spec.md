# reference-client Specification

## Purpose
Define the fictitious AcmeEdu client, whose versioned V1 and V2 databases run in Docker, so the whole migration can be generated, applied and checked without real client data.
## Requirements
### Requirement: Reproducible fictitious databases
The repository SHALL provide a fictitious client, `AcmeEdu`, whose V1 source and V2 target databases are created by versioned SQL scripts and started by a Docker Compose file, producing the same data on every start.

#### Scenario: Databases start from scratch
- **WHEN** the `AcmeEdu` compose file is started with empty volumes
- **THEN** the V1 database runs PostgreSQL 9.6 with the legacy schema and generated data
- **AND** the V2 database runs PostgreSQL 16 with the new schema and its seeded domain tables
- **AND** both are reachable from the `project-hermes` container on the `hermes-net` network

#### Scenario: Data is deterministic
- **WHEN** the V1 database is recreated
- **THEN** every table has the same row count and the same content as in the previous creation

### Requirement: Legacy data covers common cleanup cases
The V1 data SHALL include, in a deterministic share of rows, the inconsistencies the core strategies are meant to clean: names in upper case with extra spaces, masked and unmasked CPF, phones in several formats, dates stored as text including empty and impossible dates, e-mails mixed with other text, single-letter codes, and text with quotes, semicolons and backslashes.

#### Scenario: Cleanup cases are present
- **WHEN** the V1 data is generated
- **THEN** each listed inconsistency appears in at least one row of the table that should contain it

### Requirement: Migration exercises the core end to end
The `AcmeEdu` migration script SHALL migrate every V1 table to the V2 structure through core APIs, including `TableMigration`, `MultiTargetMigration`, field strategies, a reference map loaded from the V2, the `pg_dump` path for a compatible table, trigger handling, index handling and sequence resets.

#### Scenario: Full run generates and applies
- **WHEN** `python3 Migracao.py AcmeEdu --apply` runs in the `project-hermes` container against the `AcmeEdu` databases
- **THEN** generation ends with status `success`
- **AND** the application ends with result `committed` and no failing statement

#### Scenario: People are grouped
- **WHEN** a teacher has the same CPF digits as a student
- **THEN** the V2 has one `people` row for both
- **AND** the `students` and `teachers` rows reference that same `person_id`

#### Scenario: Debug run without dump
- **WHEN** the migration runs with `--debug` and `--dry-run`
- **THEN** generation skips `pg_dump` and the dry run ends with result `rolled_back`

### Requirement: Loaded data can be checked
The repository SHALL provide a check script that reads the V1 and the V2 and reports, per check, the expected value computed from the V1 and the value found in the V2, for row counts, cleaned fields, preserved text, foreign keys, sequences and index consistency.

#### Scenario: Check after a full load
- **WHEN** the check script runs after a committed full load
- **THEN** every row count matches the expected V1 count, adjusted for rows split into new tables
- **AND** no CPF or phone contains a non-digit character
- **AND** every foreign key value exists in the referenced table
- **AND** every sequence is greater than or equal to the highest `id` of its table

#### Scenario: A check fails
- **WHEN** any check finds a difference
- **THEN** the script exits with a non-zero code and names the check

#### Scenario: Index consistency is reported
- **WHEN** the check script runs after a load that used the index helper
- **THEN** it reports the result of `amcheck` heap verification for every V2 index

