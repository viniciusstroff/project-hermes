## 1. Adapter Contracts

- [x] 1.1 Create `core/adapters/` with `SourceAdapter` and `TargetAdapter` protocol definitions.
- [x] 1.2 Create `core/dialects/` with a base dialect contract for value serialization, identifier quoting, and limit rendering.
- [x] 1.3 Create `core/metadata/` with a `MetadataProvider` protocol for reference maps, triggers, indexes, and sequences.
- [x] 1.4 Export the new contracts from package `__init__.py` files for stable imports.

## 2. Concrete PostgreSQL and Firebird Implementations

- [x] 2.1 Implement `PostgresSourceAdapter` preserving named cursor and `itersize` behavior from the current engine.
- [x] 2.2 Implement `FirebirdSourceAdapter` preserving dictionary row adaptation from the current Firebird cursor adapter.
- [x] 2.3 Implement `PostgresDialect` preserving current value escaping and PostgreSQL debug limit behavior.
- [x] 2.4 Implement `FirebirdDialect` for source-side row limiting behavior used by debug mode.
- [x] 2.5 Implement `PostgresTargetAdapter` for generating PostgreSQL insert statements through the target adapter contract.
- [x] 2.6 Implement `PostgresMetadataProvider` for trigger commands, reference map loading, and sequence helpers.

## 3. Engine and Migration Classes

- [x] 3.1 Refactor `MigrationEngine` to accept source and target adapters instead of raw source connections and a source type flag.
- [x] 3.2 Move cursor creation, scalar execution, and limit clause rendering out of `MigrationEngine` into `SourceAdapter`.
- [x] 3.3 Update `TableMigration` to use the target adapter for insert statement creation.
- [x] 3.4 Update `ExpandMigration` to keep existing expansion behavior while using the refactored table insert path.
- [x] 3.5 Update `MultiTargetMigration` to use the target adapter for each target insert.

## 4. Field Strategy Serialization

- [x] 4.1 Add an adapter-aware path for field strategies to return raw Python values or explicitly rendered SQL values.
- [x] 4.2 Preserve temporary compatibility for existing `FieldStrategy.value(row)` implementations.
- [x] 4.3 Move shared SQL value serialization from `core/strategies.py` and `BaseMigration.sql_value()` into `PostgresDialect`.
- [x] 4.4 Add coverage for strings with quotes, backslashes, nulls, booleans, integers, floats, dates, and strategy transforms.

## 5. BaseMigration and Metadata Flow

- [x] 5.1 Refactor `BaseMigration` to build default PostgreSQL V1, Firebird V1, and PostgreSQL V2 adapters from the existing `.env` keys.
- [x] 5.2 Preserve transitional legacy connection attributes for Acmes that still reference `pg_v1_conn`, `pg_v2_conn`, or `fb_v1_conn`.
- [x] 5.3 Update `_engine` and `_fb_engine` helpers to return engines backed by source adapters and the configured target adapter.
- [x] 5.4 Refactor `load_reference_map()` to delegate target lookups to `MetadataProvider` while preserving cache behavior.
- [x] 5.5 Refactor trigger command generation to use `PostgresMetadataProvider`.
- [x] 5.6 Provide sequence reset helpers through `MetadataProvider` for Acmes to use instead of handwritten PostgreSQL snippets.

## 6. Acme Model and Documentation

- [x] 6.1 Update the Acme model migration to use adapter-first helpers where applicable.
- [x] 6.2 Replace PostgreSQL-specific sequence reset snippets in the Acme model with metadata provider helpers.
- [x] 6.3 Document the adapter architecture in `docs/arquitetura.md` or a focused architecture note.
- [x] 6.4 Document the transitional status of legacy connection attributes and the preferred helper APIs for new Acmes.

## 7. Verification

- [x] 7.1 Add unit tests for source adapters, target adapter, dialect serialization, and metadata provider behavior.
- [x] 7.2 Add migration-class tests proving `TableMigration`, `ExpandMigration`, and `MultiTargetMigration` generate the same PostgreSQL SQL as before.
- [x] 7.3 Add a regression test or static check that `MigrationEngine` no longer imports `psycopg2` or branches on Firebird source flags.
- [x] 7.4 Run the available test suite and record any environment-dependent gaps.
- [x] 7.5 Run `openspec status --change "tornar-migracao-agnostica-adapters"` and confirm the change is apply-ready.
