## 1. Prerequisites

- [ ] 1.1 Confirm that `preservar-barra-invertida-nos-literais` is merged into `main`.

## 2. Dialect

- [ ] 2.1 Add `UnsupportedValueError` (a `TypeError`) to `core/dialects/`.
- [ ] 2.2 In `PostgresDialect.serialize_value`, check `bool` before `int` and `datetime` before `date`, and raise `UnsupportedValueError` for any type outside the supported list.
- [ ] 2.3 Render `bytes`, `bytearray` and `memoryview` as `decode('<hex>', 'hex')`.
- [ ] 2.4 Render float `NaN` and infinities as typed `float8` literals, finite `Decimal` without quotes and special `Decimal` as typed `numeric` literals.
- [ ] 2.5 Render `date`, `datetime` and `time` as quoted ISO 8601 text and `UUID` as quoted canonical text.
- [ ] 2.6 Raise `UnsupportedValueError` for text with a NUL character, including its position.

## 3. Error Context

- [ ] 3.1 Add `ValueSerializationError` and raise it from `_field_value()` in `TableMigration` and `MultiTargetMigration` with the target table, the column, the Python type and the reason, without the value.
- [ ] 3.2 Log once per target table and column when a binary value is rendered, through the engine progress or logger channel.

## 4. Documentation

- [ ] 4.1 List the supported types and their literals in `docs/estrategias.md`, with the `Transform` examples for NUL and for binary text columns.
- [ ] 4.2 Update item 8 of `docs/melhorias-futuras.md`, removing the types handled by this change from the pending list.

## 5. Verification

- [ ] 5.1 Add dialect tests for each supported type, including timezone-aware datetimes, empty bytes and `Decimal` with exponent.
- [ ] 5.2 Add tests for NUL text and for `list`, `dict` and `timedelta` values.
- [ ] 5.3 Add a `TableMigration` test checking that the error names table, column and type and does not contain the value.
- [ ] 5.4 Apply a generated run with `bytea`, `NaN` and `Decimal` values to a PostgreSQL 16 target and read them back, comparing with the original Python values.
- [ ] 5.5 Generate a run before and after this change (with `AcmeEdu` if it exists) and diff the SQL; only `Decimal` literals should change unless the source has the affected types.
- [ ] 5.6 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core Acme tests`.
- [ ] 5.7 Run `openspec validate serializar-tipos-especiais-no-dialeto`.
