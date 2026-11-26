## 1. Prerequisites

- [ ] 1.1 Archive `desacoplar-migracao-de-banco-e-versao`, so the deltas of this change apply over the specs it creates.

## 2. Shared Execution

- [ ] 2.1 Move the modes, the `stop` and `continue` policies and the command item from `core/apply/applier.py` to `core/apply/execution.py`, keeping `check_capabilities()` in `core/application.py`.
- [ ] 2.2 Make `ArtifactApplier` use the shared module, keeping its existing tests unchanged.
- [ ] 2.3 Add `source` (`generation` or `artifacts`) to `ApplicationReport` and `relatorio.json`.

## 3. Target Sessions

- [ ] 3.1 Change `TargetSession.commands()` to read an iterable of lines with a first line number, in the contract, in `PostgresSession` and in `MongoSession`, and make the applier pass the open artifact file.
- [ ] 3.2 Keep the last `standard_conforming_strings` read by the PostgreSQL splitter between calls to `commands()`, and go back to the server value after `end_artifact()`.
- [ ] 3.3 Add `waits_between_commands=False` to `session()` of both target adapters. In PostgreSQL, set `idle_in_transaction_session_timeout` to zero in `begin()` and after each `RESET ALL` when it is true.
- [ ] 3.4 Add `waits_between_commands` to `session_opener()` in `core/apply/cli.py`.

## 4. Direct Database Output

- [ ] 4.1 Create `core/outputs/direct.py` with `DirectDatabaseOutput(target, open_session, run_dir, mode, on_error)`, checking the declared capabilities of the target adapter when it is built.
- [ ] 4.2 Keep `setup` operations in memory, then open the session, check its capabilities, begin the transaction and run them first when `prepare_target` opens.
- [ ] 4.3 Render each operation through the target adapter, read the text with `session.commands()` from the current line of the phase and hand each command to the policy, recording skipped commands and refusing commands with `error`.
- [ ] 4.4 On a phase change, flush the policy, call `end_artifact()` and close the phase in the report, naming each phase with `artifact_name()`.
- [ ] 4.5 Run an included file through `session.commands()` over the open file, continuing the line count of the phase.
- [ ] 4.6 Implement `flush()`, `finish(success)` and `close()`: commit only on `finish(True)` in `apply`, roll back in every other case, report written in all cases.
- [ ] 4.7 Implement `describe()` with type, adapter, mode, error policy and report directory, and show the MongoDB transaction limit on the console when the session opens.

## 5. Selection

- [ ] 5.1 Add `output_settings(settings)` to `core/context.py`, a context manager backed by a `ContextVar` that the default factory reads.
- [ ] 5.2 Make `DefaultMigrationContextFactory` build `FileOutput`, `DirectDatabaseOutput` or `MirrorOutput(FileOutput, DirectDatabaseOutput)` from the settings, recording connection errors in the manifest as it does for the other connections.
- [ ] 5.3 Add `--output file|direct|hybrid` to `Migracao.py`, require `--apply` or `--dry-run` with `direct` and `hybrid`, and skip the post-generation application in those cases.
- [ ] 5.4 Print the target, mode and policy before a direct run, and the result, error count and report directory after it.

## 6. Documentation

- [ ] 6.1 Document `--output`, the meaning of `--apply` and `--dry-run` with each output, the lock duration, the MongoDB transaction limit and the limitation of reads through `context.target.connection` in `docs/execucao.md`.
- [ ] 6.2 Document `DirectDatabaseOutput`, `commands()` over lines, `waits_between_commands` and the shared execution in `docs/arquitetura.md`.
- [ ] 6.3 Mark item 2 of `docs/melhorias-futuras.md` as implemented and update `README.md`.

## 7. Verification

- [ ] 7.1 Add session tests: `commands()` over a file and over rendered text give the same commands and lines, `standard_conforming_strings` carries over between calls, and `waits_between_commands` sets the idle limit in PostgreSQL only.
- [ ] 7.2 Add `DirectDatabaseOutput` tests with a fake session: commands run before `write()` returns, held `setup`, phase change, included file, deferred batch failure, both modes, both policies, refused capabilities, `close()` without `finish(True)` and failing commit.
- [ ] 7.3 Add an equivalence test: the same operations through the direct output and through `ArtifactApplier` over the files run the same commands with the same skip counters.
- [ ] 7.4 Add CLI tests for the `--output` combinations and the context factory.
- [ ] 7.5 Run AcmeEdu with `hybrid --dry-run` and `direct --apply` against PostgreSQL and compare with `Aplicar.py` over the same artifacts. Run `direct --apply` against the MongoDB of the matrix and check with `conferir.py`.
- [ ] 7.6 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core AcmeEdu tests`.
- [ ] 7.7 Run `openspec validate introduzir-direct-database-output --strict`.
