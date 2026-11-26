## MODIFIED Requirements

### Requirement: Migration exercises the core end to end
The `AcmeEdu` migration script SHALL migrate every V1 table to the V2 structure through core APIs, including `TableMigration`, `MultiTargetMigration`, field strategies, a reference map loaded from the V2, the copy of a compatible entity, the suspension of triggers and identity resets, using only `SourceQuery`, operations and strategies, with no SQL of its own.

#### Scenario: Full run generates and applies
- **WHEN** `python3 Migracao.py AcmeEdu --apply` runs in the `project-hermes` container against the `AcmeEdu` databases
- **THEN** generation ends with status `success`
- **AND** the application ends with result `committed` and no failing statement

#### Scenario: People are grouped
- **WHEN** a teacher has the same CPF digits as a student
- **THEN** the V2 has one `people` row for both
- **AND** the `students` and `teachers` rows reference that same `person_id`
- **AND** the grouping is computed in Python from a neutral read of the students, keeping the student with the lowest code

#### Scenario: Version and database matrix
- **WHEN** the matrix runner executes the script from PostgreSQL 9.6 to 16, from PostgreSQL 17 to 17, from PostgreSQL to MongoDB, from MongoDB to PostgreSQL and from Firebird 3 and 5 to PostgreSQL
- **THEN** every path ends its dry run with `rolled_back`, its apply with `committed` and its check script with every check passing
- **AND** only the `.env` changes between the paths

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

### Requirement: Loaded data can be checked
The repository SHALL provide a check script that reads the V1 and the V2 through their adapters and reports, per check, the expected value computed from the V1 and the value found in the V2, for row counts, cleaned fields, preserved text and foreign keys, plus sequences and index consistency when the target is PostgreSQL.

#### Scenario: Check after a full load
- **WHEN** the check script runs after a committed full load
- **THEN** every row count matches the expected V1 count, adjusted for rows split into new tables
- **AND** no CPF or phone contains a non-digit character
- **AND** every foreign key value exists in the referenced table
- **AND** with a PostgreSQL target, every sequence is greater than or equal to the highest `id` of its table

#### Scenario: A check fails
- **WHEN** any check finds a difference
- **THEN** the script exits with a non-zero code and names the check

#### Scenario: Index consistency is reported
- **WHEN** the check script runs with a PostgreSQL target
- **THEN** it reports the result of `amcheck` heap verification for every V2 index

#### Scenario: MongoDB target
- **WHEN** the check script runs with a MongoDB target
- **THEN** it runs every check except sequences and index consistency, reading the collections through the MongoDB adapter
