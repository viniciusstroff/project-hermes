## 1. Output Contracts

- [x] 1.1 Create `core/outputs/` with an `OutputAdapter` protocol for phase opening, writing, comments, flush, and close.
- [x] 1.2 Implement `SqlFileOutput` preserving the current SQL artifact file mapping and buffered writes.
- [x] 1.3 Remove `SqlArtifactWriter`; `SqlFileOutput` is the only SQL file output.
- [x] 1.4 Export `OutputAdapter` and `SqlFileOutput` from stable package `__init__.py` files.

## 2. Runtime Integration

- [x] 2.1 Update `MigrationContext` to expose `output` as the preferred output dependency.
- [x] 2.2 Remove `context.writer`; `context.output` is the only output API on the context.
- [x] 2.3 Update `DefaultMigrationContextFactory` to build `SqlFileOutput` by default.
- [x] 2.4 Update `MigrationContext.engine()` to write through the configured output adapter.
- [x] 2.5 Update `MigrationContext.close()` to close the configured output adapter.

## 3. Runner and BaseMigration Compatibility

- [x] 3.1 Update `MigrationRunner` to call output methods for phase opening, comments, writes, and flush.
- [x] 3.2 Update `BaseMigration.write_sql()`, `print_comment()`, `_open_sql_file()`, `_flush_writes()`, and `file` to delegate to output.
- [x] 3.3 Preserve current generated file names and phase order for inherited and composed migrations.

## 4. Documentation

- [x] 4.1 Document the output adapter architecture in `docs/arquitetura.md`.
- [x] 4.2 Update `docs/melhorias-futuras.md` to mark output adapter v1 as the current next step and leave direct execution as future work.
- [x] 4.3 Document `context.output` as the output API and `BaseMigration.write_sql()` as the inheritance facade helper.

## 5. Verification

- [x] 5.1 Add unit tests for `SqlFileOutput` phase opening, buffered writing, comments, flush, and close.
- [x] 5.2 Add integration tests proving `MigrationContext` and `MigrationRunner` use the configured output adapter.
- [x] 5.3 Add regression tests proving existing `BaseMigration` artifact behavior remains unchanged.
- [x] 5.4 Run `python3 -m unittest discover -s tests`.
- [x] 5.5 Run `python3 -m compileall core Acme tests`.
- [x] 5.6 Run `openspec status --change "introduzir-output-adapter-sql-file"` and confirm the change is apply-ready.
