## MODIFIED Requirements

### Requirement: Migration exercises the core end to end
The `AcmeEdu` migration script SHALL migrate every V1 table to the V2 structure through core APIs, including `TableMigration`, `MultiTargetMigration`, field strategies, a reference map loaded from the V2, the copy of a compatible entity, the suspension of triggers and identity resets.

#### Scenario: Full run generates and applies
- **WHEN** `python3 Migracao.py AcmeEdu --apply` runs in the `project-hermes` container against the `AcmeEdu` databases
- **THEN** generation ends with status `success`
- **AND** the application ends with result `committed` and no failing statement

#### Scenario: People are grouped
- **WHEN** a teacher has the same CPF digits as a student
- **THEN** the V2 has one `people` row for both
- **AND** the `students` and `teachers` rows reference that same `person_id`

#### Scenario: Compatible entity by inclusion
- **WHEN** the migration lists `holidays` in `compatible_entities()`
- **THEN** only `holidays` is copied in the `copy_compatible_entities` phase
- **AND** no V1 table of the old layout needs to be listed to stay out

#### Scenario: Native copy gives the same artifact
- **WHEN** the client `.env` sets `SOURCE_V1_NATIVE_COPY=pg_dump`
- **THEN** the `copy_compatible_entities` artifact is the `pg_dump` output for `holidays`
- **AND** the check script passes as with the row by row copy

#### Scenario: Debug run with a limited copy
- **WHEN** the migration runs with `--debug` and `--dry-run`
- **THEN** generation copies `holidays` row by row up to the limit and the dry run ends with result `rolled_back`
