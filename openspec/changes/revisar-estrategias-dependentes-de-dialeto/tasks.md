## 1. Prerequisites and Dialect Identity

- [ ] 1.1 Confirm that `preservar-barra-invertida-nos-literais` is merged into `main` before changing `RegexClean`.
- [ ] 1.2 Add `name` to the `Dialect` protocol, with `postgres` in `PostgresDialect` and `firebird` in `FirebirdDialect`.
- [ ] 1.3 Declare the `dialect` attribute in the `SourceAdapter` protocol.

## 2. Strategies Without SQL

- [ ] 2.1 Remove `FieldStrategy.value()`, `FieldStrategy.render()`, `sql_value()` and `_POSTGRES_DIALECT` from `core/strategies.py`.
- [ ] 2.2 Make `_field_value()` in `TableMigration` and `MultiTargetMigration` serialize `raw_value(row)` through `engine.target_adapter`, and require `select_columns`, `insert_col` and `raw_value` in their field validation.
- [ ] 2.3 In `RegexClean`, remove the `render()` and `value()` overrides and the backslash removal, and add `v2_name`; make `CpfClean` and `PhoneClean` pass `v2_name` to it.
- [ ] 2.4 Replace `SqlExpression` with `SourceSqlExpression(expression, col, dialect)`, exposing `source_dialect`.
- [ ] 2.5 Check `source_dialect` against the source adapter dialect in `build_source_sql()` of `TableMigration` and `MultiTargetMigration`, before the progress count and the source query.
- [ ] 2.6 Make `DateConvert` return `datetime.date`, or `datetime.datetime` with `as_datetime=True`, and remove `to_fmt`.

## 3. Portable Strategies

- [ ] 3.1 Add `NullIfBlank`, `Truncate`, `Upper`, `Lower` and `Default` to `core/strategies.py`, each with `v2_name`.

## 4. Documentation

- [ ] 4.1 Split `docs/estrategias.md` into portable and dialect-dependent strategies, replace the `SqlExpression` examples with portable strategies, document the known differences from the SQL functions and remove the `sql_value()` section.
- [ ] 4.2 Update the strategy import line and examples in `docs/novo-cliente.md`.
- [ ] 4.3 State in `docs/arquitetura.md` that strategies return Python values and list the dialect-dependent escape hatches.
- [ ] 4.4 Mark item 8 of `docs/melhorias-futuras.md` as implemented, pointing to `serializar-tipos-especiais-no-dialeto` for the remaining types.

## 5. Verification

- [ ] 5.1 Rewrite the strategy tests that call `value()`, `render()` or `sql_value()` to check `raw_value()`, and the rendering through `PostgresTargetAdapter.serialize_value()` where the literal matters.
- [ ] 5.2 Add tests for the portable strategies with `None`, empty and blank text, non-text values and accented text.
- [ ] 5.3 Add `SourceSqlExpression` tests with PostgreSQL and Firebird source adapters, checking that a dialect mismatch raises before any query reaches the fake source.
- [ ] 5.4 Add `RegexClean` tests for a pattern that keeps the backslash and for `v2_name`.
- [ ] 5.5 Add `DateConvert` tests for date, datetime and invalid values, including the PostgreSQL rendering.
- [ ] 5.6 Add a validation test for a field object with `value()` or `render()` and no `raw_value()`.
- [ ] 5.7 Generate a run before and after this change (with `AcmeEdu` if it exists) and diff the SQL; only `RegexClean`, `CpfClean` and `PhoneClean` literals should change from `E'...'` to `'...'`.
- [ ] 5.8 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core Acme tests`.
- [ ] 5.9 Run `openspec validate revisar-estrategias-dependentes-de-dialeto`.
