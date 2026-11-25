## 1. Declaration and Loaded Map

- [ ] 1.1 Create `core/references.py` with the frozen `ReferenceMap` declaration, requiring exactly one of `table` or `query`, and `key` and `value` when `table` is used.
- [ ] 1.2 Add `LoadedReferenceMap`, a read-only `Mapping` that exposes `name` and `normalize_key(raw)`.
- [ ] 1.3 Add the function that builds a `LoadedReferenceMap` from a declaration and key/value pairs: skip null keys, apply the normalizers and reject a normalized key with two different values.
- [ ] 1.4 Add `ReferenceMapError` and `TargetReadClosedError`.

## 2. Metadata Provider

- [ ] 2.1 Replace `load_reference_map(sql, ...)` with `load_reference_pairs(reference)` in the `MetadataProvider` contract and add `close_reads()`.
- [ ] 2.2 In `PostgresMetadataProvider`, build the table-based `SELECT` with `quote_identifier` and render each filter value with `serialize_value`; run `query` declarations as given.
- [ ] 2.3 Make every `PostgresMetadataProvider` method that queries the target raise `TargetReadClosedError` after `close_reads()`, keeping cached trigger commands available.
- [ ] 2.4 Make `close_reads()` end the provider read transaction with `ROLLBACK`, so it holds no locks on the target.

## 3. Context, Runner and Facade

- [ ] 3.1 Add `MigrationContext.load_reference_maps(declarations)`: reject repeated names, load each map once, log name and key count and warn on empty maps.
- [ ] 3.2 Add `MigrationContext.reference_map(name)`, raising `ReferenceMapError` for a name that was not declared.
- [ ] 3.3 In `MigrationRunner`, call `declare_reference_maps()`, load the maps and call `close_reads()` after the trigger commands and before `set_indexes_commands` and `prepare_target`.
- [ ] 3.4 Make a declaration or load error end the run with a `failed` manifest naming the map, without opening `prepare_target`.
- [ ] 3.5 Add `BaseMigration.declare_reference_maps()` returning `[]` and `BaseMigration.reference_map(name)`.
- [ ] 3.6 Remove `load_reference_map()` from `MigrationContext` and `BaseMigration`, the free-text `_reference_maps` cache and the `BaseMigration._reference_maps` attribute.

## 4. Lookup

- [ ] 4.1 Make `Lookup` use `normalize_key` of a `LoadedReferenceMap` when no `key_normalizer` is given, keeping the explicit normalizer and plain `dict` behavior unchanged.

## 5. Documentation

- [ ] 5.1 Rewrite the `Lookup` section of `docs/estrategias.md` with a declared map and without the duplicated normalizer.
- [ ] 5.2 Add `declare_reference_maps()` to the checklist in `docs/novo-cliente.md`.
- [ ] 5.3 Document in `docs/arquitetura.md` when the maps load, the closed target reads and the `pg_v2_conn` limitation; update the "Por que depois da geração" section.
- [ ] 5.4 Mark item 7 of `docs/melhorias-futuras.md` as implemented, update the dependency in item 2 and update "Próximo passo" in `README.md`.
- [ ] 5.5 Add a declared map and a `Lookup` that uses it to the `Acme` model.

## 6. Verification

- [ ] 6.1 Add tests for declaration validation, map building (normalization, null keys, ambiguous and repeated keys) and read-only access.
- [ ] 6.2 Add `PostgresMetadataProvider` tests with a fake connection for the rendered `SELECT`, filter literals, quoted identifiers, `query` declarations and reads after `close_reads()`.
- [ ] 6.3 Add runner tests: maps load before `prepare_target`, undeclared map error, failed manifest on load error and a run without declared maps.
- [ ] 6.4 Add `Lookup` tests with a `LoadedReferenceMap`, with and without explicit `key_normalizer`.
- [ ] 6.5 If `AcmeEdu` exists, declare its `student_statuses` map, generate a run and diff its SQL against a run generated before this change; no difference is expected.
- [ ] 6.6 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core Acme tests`.
- [ ] 6.7 Run `openspec validate padronizar-lookups-de-referencia`.
