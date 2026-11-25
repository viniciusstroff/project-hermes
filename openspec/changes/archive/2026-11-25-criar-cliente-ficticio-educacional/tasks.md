## 1. Optional Firebird

- [x] 1.1 In `core/context.py`, connect to Firebird and register the `fb_v1` source only when `FB_V1_HOST` is non-empty.
- [x] 1.2 Make `context.engine(name)` raise a clear error for a source that is not configured.
- [x] 1.3 Add tests for a context without Firebird keys and for the error on a missing source.

## 2. Databases

- [x] 2.1 Write `AcmeEdu/banco/v1_esquema.sql` with the legacy tables, compatible with PostgreSQL 9.6.
- [x] 2.2 Write `AcmeEdu/banco/v1_dados.sql` with `setseed()` and `generate_series()`, covering every cleanup case of the design.
- [x] 2.3 Write `AcmeEdu/banco/v2_esquema.sql` with the new tables, `id` keys with `<table>_id_seq` sequences, foreign keys, indexes and the seeded `student_statuses`.
- [x] 2.4 Write `AcmeEdu/docker-compose.yml` with `acmeedu-v1` (PostgreSQL 9.6) and `acmeedu-v2` (PostgreSQL 16) on `hermes-net`.
- [x] 2.5 Write `AcmeEdu/.env.example` pointing `PG_V1_*` and `PG_V2_*` to the compose services, without Firebird keys.

## 3. Migration Script

- [x] 3.1 Write `AcmeEdu/AcmeEdu.py` with the dump exclusions, the initial `TRUNCATE`s and one method per V2 table, in foreign key order.
- [x] 3.2 Load the `student_statuses` map from the V2 with `load_reference_map`.
- [x] 3.3 Keep the client-specific transformations (name case, term split, attendance, degree) as pure functions.
- [x] 3.4 Use the index helper as real clients do and `write_reset_sequences` with `column='id'`.
- [x] 3.5 Add tests for the pure functions and for the SQL generated for one row of each table, using fake source rows.

## 4. Check

- [x] 4.1 Write `AcmeEdu/conferir.py`, which reads the V1 and the V2 from the client `.env` and checks row counts, cleaned fields, preserved backslashes, foreign keys, sequences and `amcheck` on every index.
- [x] 4.2 Exit with a non-zero code when a check fails, naming the check.

## 5. End-to-End Run

- [x] 5.1 Start the databases and run `Migracao.py AcmeEdu --debug 100 --dry-run` in the `project-hermes` container.
- [x] 5.2 Run the full generation, a dry run with `Aplicar.py` and then the apply, and run `conferir.py`.
- [x] 5.3 Record the results of the index consistency check and answer the index questions of the design.
- [x] 5.4 Use the full run to close task 6.4 of `aplicar-artefatos-sql-no-destino`.
- [x] 5.5 Generate with and without the backslash fix and diff the SQL to close task 3.2 of `preservar-barra-invertida-nos-literais`.

## 6. Documentation

- [x] 6.1 Write `AcmeEdu/README.md` with the scenario, the V1 → V2 mapping and the commands for the full run.
- [x] 6.2 Mention `AcmeEdu` in the main README and in `docs/novo-cliente.md` as the runnable example.
- [x] 6.3 Document in `docs/execucao.md` that the Firebird keys are optional.

## 7. Verification

- [x] 7.1 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core Acme AcmeEdu tests`.
- [x] 7.2 Run `openspec validate criar-cliente-ficticio-educacional`.
