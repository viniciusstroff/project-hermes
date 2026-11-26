## 1. Preconditions

- [x] 1.1 Archive `aplicar-artefatos-sql-no-destino`, `preservar-barra-invertida-nos-literais` and `criar-cliente-ficticio-educacional`, so the capabilities modified here exist in `openspec/specs/`.
- [x] 1.2 Implement `revisar-estrategias-dependentes-de-dialeto` and `padronizar-lookups-de-referencia`.
- [ ] 1.3 Revise `introduzir-direct-database-output` so the direct output executes operations through the adapter session. (Phase 2 recorded the dependency and switched it to `TARGET_*`; the rewrite on top of `TargetSession` waits for phase 6, when that contract exists.)

## 2. Named Connections and Adapter Registry

- [x] 2.1 Parse `SOURCE_<NAME>_*` and `TARGET_*` groups from the client `.env` into named connection configs.
- [x] 2.2 Create `core/adapters/registry.py` with `postgres`, `firebird` and `mongodb`, plus `package.module:Class` paths checked against the contracts. (The `mongodb` entry is added with its adapter in phase 8.)
- [x] 2.3 Move every driver import into the adapter modules, imported only on connect; remove the imports from `core/context.py` and `core/base_migration.py`.
- [x] 2.4 Replace `PG_V1_*`, `PG_V2_*` and `FB_V1_*` in the context factory, the manifest and `.env.example` files; expose `context.source(name)`.
- [x] 2.5 Add tests for parsing, unknown and missing keys, third-party paths and imports without drivers.

## 3. Neutral Reading

- [x] 3.1 Add `SourceQuery`, `NativeQuery` and `NativeField`, and the `read`/`count` source contract.
- [x] 3.2 Port the PostgreSQL and Firebird source adapters, including the integer limit and the refusal of native queries for other adapters.
- [x] 3.3 Replace `source_sql` with `source=` in `TableMigration`, `ExpandMigration` and `MultiTargetMigration`, and the string debug limit with an integer limit applied by the context.
- [x] 3.4 Add tests per adapter for field lists, equality filters, ordering, limits and native query refusal.

## 4. Write Operations and Artifacts

- [x] 4.1 Add the operation types and the target contract with `render`, `read`, `session`, `capabilities` and `artifact_format`. (`session` and `capabilities` come with the applier in 6.1, where they are used.)
- [x] 4.2 Port the PostgreSQL target: inserts through the dialect with identifiers quoted by `quote_identifier`, `Clear`, `SuspendConstraints` from the catalog, `ResetIdentity` with `pg_get_serial_sequence`.
- [x] 4.3 Remove `MetadataProvider`, `set_index_commands_for` and the legacy connection attributes; add the facade shortcuts and `emit()`. (The legacy connection attributes already left in phase 2, together with the fixed `.env` keys.)
- [x] 4.4 Replace `SqlFileOutput` with `FileOutput` writing `comandos/<NN>_<phase>.<extension>`, and make `OutputAdapter.write` receive operations.
- [x] 4.5 Record sources, target, server versions and the artifact list in the manifest. (PostgreSQL reports its version now; Firebird does in 7.1.)
- [x] 4.6 Port `Acme` to the new API and update the tests that use the removed helpers.

## 5. Compatible Entities

- [x] 5.1 Add `compatible_entities()` with optional renaming and the generic copy in the `copy_compatible_entities` phase. (`CopyEntity` names its source; debug mode always copies row by row.)
- [x] 5.2 Add the optional `pg_dump` native copy to the PostgreSQL source adapter, with the major version check against the source server.
- [x] 5.3 Remove `dump_tables()`, `dump_ignoring_tables()` and `dump_non_existing_tables()`.

## 6. Application by Capabilities

- [x] 6.1 Add `Capabilities`, `ArtifactFormat` readers and the `TargetSession` contract. (The reader is `commands()` of the session, because splitting SQL depends on a server setting.)
- [x] 6.2 Move the splitter, skipped statements, `RESET ALL` and `standard_conforming_strings` into the PostgreSQL adapter.
- [x] 6.3 Make the applier read artifacts from the manifest, refuse modes the target cannot honor and pick the default dry-run policy from the capabilities.
- [x] 6.4 Make `Aplicar.py` read the adapter from the manifest and the connection from `TARGET_*`, make `Migracao.py --apply` and `--dry-run` use the target connection of the client `.env`, and report adapter error codes instead of `SQLSTATE`.

## 7. Version Independence

- [x] 7.1 Read the server version on connect in each adapter and enforce the declared ranges.
- [x] 7.2 Move version-dependent behavior (Firebird row limit, PostgreSQL dump headers) behind adapter methods. (`ROWS` works from 2.0, so the Firebird limit needs no version branch; the PostgreSQL session skips settings the target server does not have.)
- [x] 7.3 Remove `postgresql-client-9.6` from the `Dockerfile`, with an optional build argument for native copy, and split drivers into `requirements/<adapter>.txt`.

## 8. MongoDB Adapter

- [x] 8.1 Write `core/adapters/mongodb.py` with `find`, `aggregate` for native queries and `count_documents`.
- [x] 8.2 Render operations as relaxed Extended JSON lines and read them back.
- [x] 8.3 Apply in a session transaction, grouping consecutive inserts into `insert_many` batches and mapping bulk errors back to command numbers.
- [x] 8.4 Detect replica set support for the transaction capability and read `transactionLifetimeLimitSeconds`.
- [x] 8.5 Add tests with a fake client and an integration test against MongoDB 7 in Docker. (`tests/test_mongodb_integration.py` runs with `MONGODB_TEST_URI` and `MONGODB_STANDALONE_URI`; skipped otherwise.)

## 9. Reference Client and Version Matrix

- [ ] 9.1 Make `AcmeEdu.py` use only `SourceQuery`, operations and strategies, grouping teachers by CPF in Python.
- [ ] 9.2 Make `conferir.py` read both sides through the adapters, with PostgreSQL-only checks when the target is PostgreSQL.
- [ ] 9.3 Add compose services for PostgreSQL 17, MongoDB 7 as a single-node replica set and Firebird 3 and 5, and a script that fills the MongoDB V1 and the Firebird fixture from the PostgreSQL V1.
- [ ] 9.4 Run the matrix: PostgreSQL 9.6 → 16, PostgreSQL 17 → 17, PostgreSQL → MongoDB, MongoDB → PostgreSQL, Firebird 3 and 5 → PostgreSQL, each with dry run, apply and `conferir.py`.
- [ ] 9.5 Record the results of the matrix in this file.

## 10. Documentation

- [ ] 10.1 Update `docs/arquitetura.md` with the contracts, the registry, the operations and the capabilities.
- [ ] 10.2 Update `docs/execucao.md`, `docs/novo-cliente.md`, `docs/estrategias.md`, the main README and `AcmeEdu/README.md` with the new `.env` and API.
- [ ] 10.3 Write a conversion guide for private clients, with the `AcmeEdu` script before and after.
- [ ] 10.4 Update item 1 of `docs/melhorias-futuras.md`.

## 11. Verification

- [ ] 11.1 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core Acme AcmeEdu tests`.
- [ ] 11.2 Run `openspec validate desacoplar-migracao-de-banco-e-versao`.
