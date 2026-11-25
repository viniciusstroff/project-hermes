## 1. Prerequisites

- [ ] 1.1 Confirm that `padronizar-lookups-de-referencia` is implemented: reference maps load before `prepare_target` and `close_reads()` ends the provider read transaction.

## 2. Shared Execution

- [ ] 2.1 Move modes, skip rules, `COPY ... FROM stdin` detection and the `stop` and `continue` policies from `core/apply/applier.py` to `core/apply/execution.py`.
- [ ] 2.2 Make `SqlArtifactApplier` use the shared module, keeping its existing tests unchanged.
- [ ] 2.3 Add an incremental splitter API that accepts text chunks of any size, returns complete statements with their starting line and turns pending text into the last statement when the input ends.
- [ ] 2.4 Add `source` (`generation` or `artifacts`) to `ApplicationReport` and `relatorio.json`.

## 3. Output Contract and Runner

- [ ] 3.1 Add `include_file(phase, path)` to `OutputAdapter`, as a no-op in `SqlFileOutput` and forwarded in order by `MirrorOutput`.
- [ ] 3.2 Make `BaseMigration.dump_tables()` record the produced dump path in the migration context.
- [ ] 3.3 Make `MigrationRunner` call `include_file('dump_compatible_tables', path)` after `run_initial()` and before opening `load_transformed_data`, only when the context has a dump path.

## 4. Direct Database Output

- [ ] 4.1 Create `core/outputs/direct_db.py` with `DirectDatabaseOutput(connect, artifacts, mode, on_error)`, opening the connection with UTF-8 encoding, `autocommit` off and `idle_in_transaction_session_timeout=0` as a startup option.
- [ ] 4.2 Read `standard_conforming_strings` from the server and pass it to the incremental splitter.
- [ ] 4.3 Keep `setup` statements in memory and execute them first when `prepare_target` opens.
- [ ] 4.4 Execute each complete statement through the shared execution, finishing the pending statement and running `RESET ALL` when a phase changes.
- [ ] 4.5 Execute an included file in streaming with the same rules and run `RESET ALL` after it.
- [ ] 4.6 Implement `finish(success)` and `close()`: `COMMIT` only on `finish(True)` in `apply`, `ROLLBACK` in every other case, report written in all cases.
- [ ] 4.7 Implement `describe()` with type, mode, error policy and report directory.

## 5. Selection

- [ ] 5.1 Add `output_settings(settings)` to `core/context.py`, a context manager backed by a `ContextVar` that the default factory reads.
- [ ] 5.2 Make `DefaultMigrationContextFactory` build `SqlFileOutput`, `DirectDatabaseOutput` or `MirrorOutput(SqlFileOutput, DirectDatabaseOutput)` from the settings, recording connection errors in the manifest as it does for the other connections.
- [ ] 5.3 Add `--output sql-file|direct-db|hybrid` to `Migracao.py`, require `--apply` or `--dry-run` with `direct-db` and `hybrid`, and skip the post-generation application in those cases.
- [ ] 5.4 Print the target, mode and policy before a direct run, and the result, error count and report directory after it.

## 6. Documentation

- [ ] 6.1 Document `--output`, the meaning of `--apply` and `--dry-run` with each output, the lock duration and the `pg_v2_conn` limitation in `docs/execucao.md`.
- [ ] 6.2 Document `DirectDatabaseOutput`, `include_file()` and the shared execution in `docs/arquitetura.md`.
- [ ] 6.3 Mark Fase 4 of item 2 of `docs/melhorias-futuras.md` as implemented and update `README.md`.

## 7. Verification

- [ ] 7.1 Add incremental splitter tests that split the same text at arbitrary points and compare with `split_sql()` over the whole text.
- [ ] 7.2 Add `DirectDatabaseOutput` tests with a fake connection: execution before `write()` returns, held `setup`, phase reset, included dump, both modes, both policies, `close()` without `finish(True)` and failing `COMMIT`.
- [ ] 7.3 Add an equivalence test: the same generated text through the direct output and through `SqlArtifactApplier` over the files executes the same statements with the same skip counters.
- [ ] 7.4 Add runner and contract tests for `include_file()` in full and debug runs, and CLI tests for `--output` combinations.
- [ ] 7.5 Run `hybrid` with `--dry-run` and `direct-db` with `--apply` against the Docker `target-db`, using `AcmeEdu` if available, and compare with `Aplicar.py` over the same artifacts.
- [ ] 7.6 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core Acme tests`.
- [ ] 7.7 Run `openspec validate introduzir-direct-database-output`.
