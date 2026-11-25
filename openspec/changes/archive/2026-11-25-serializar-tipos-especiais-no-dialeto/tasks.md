## 1. Prerequisites

- [x] 1.1 Confirm that `preservar-barra-invertida-nos-literais` is merged into `main`.

## 2. Dialect

- [x] 2.1 Add `UnsupportedValueError` (a `TypeError`) to `core/dialects/`.
- [x] 2.2 In `PostgresDialect.serialize_value`, check `bool` before `int` and `datetime` before `date`, and raise `UnsupportedValueError` for any type outside the supported list.
- [x] 2.3 Render `bytes`, `bytearray` and `memoryview` as `decode('<hex>', 'hex')`.
- [x] 2.4 Render float `NaN` and infinities as typed `float8` literals, finite `Decimal` without quotes and special `Decimal` as typed `numeric` literals.
- [x] 2.5 Render `date`, `datetime` and `time` as quoted ISO 8601 text and `UUID` as quoted canonical text.
- [x] 2.6 Raise `UnsupportedValueError` for text with a NUL character, including its position.

## 3. Error Context

- [x] 3.1 Add `ValueSerializationError` and raise it from `_field_value()` in `TableMigration` and `MultiTargetMigration` with the target table, the column, the Python type and the reason, without the value.
- [x] 3.2 Log once per target table and column when a binary value is rendered, through the engine progress or logger channel.

## 4. Documentation

- [x] 4.1 List the supported types and their literals in `docs/estrategias.md`, with the `Transform` examples for NUL and for binary text columns.
- [x] 4.2 Update item 8 of `docs/melhorias-futuras.md`, removing the types handled by this change from the pending list.

## 5. Verification

- [x] 5.1 Add dialect tests for each supported type, including timezone-aware datetimes, empty bytes and `Decimal` with exponent.
- [x] 5.2 Add tests for NUL text and for `list`, `dict` and `timedelta` values.
- [x] 5.3 Add a `TableMigration` test checking that the error names table, column and type and does not contain the value.
- [x] 5.4 Apply a generated run with `bytea`, `NaN` and `Decimal` values to a PostgreSQL 16 target and read them back, comparing with the original Python values. Done in 2026-11-25: 50 values (bytes with backslash, quote and NUL, empty bytes, `memoryview`, `NaN`, infinities, `Decimal` with exponent, dates with time zone, time, UUID) were applied with 0 errors and read back equal. With `standard_conforming_strings` off, `decode` still works, and a `bytea` value in a `text` column is stored as `\x4142`.
- [x] 5.5 Generate a run before and after this change (with `AcmeEdu` if it exists) and diff the SQL; only `Decimal` literals should change unless the source has the affected types. Done in 2026-11-25 with `AcmeEdu`: `01`, `02` and `04` were identical; in `03` only 5,960 `enrollments` lines changed, all by removing quotes around numbers. The new run applied with 0 errors and `conferir.py` passed 58 of 58 checks.
- [x] 5.6 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core Acme tests`.
- [x] 5.7 Run `openspec validate serializar-tipos-especiais-no-dialeto`.
