## 1. Output Contract

- [x] 1.1 Add `finish(success: bool)` and `describe()` to the `OutputAdapter` protocol, documenting that `close()` without `finish(True)` means an unconfirmed run.
- [x] 1.2 Implement `SqlFileOutput.finish()` as a flush for both results and `SqlFileOutput.describe()` returning `{'type': 'sql-file'}`.

## 2. Mirror Output

- [x] 2.1 Create `core/outputs/mirror.py` with `MirrorOutput(*outputs)`, raising `ValueError` when no output is given.
- [x] 2.2 Forward `open_phase`, `write`, `comment` and `flush` in order, stopping on the first error.
- [x] 2.3 Forward `finish` and `close` to every output even when one fails, raising the first error at the end.
- [x] 2.4 Implement `describe()` returning `{'type': 'mirror', 'outputs': [...]}`.
- [x] 2.5 Export `MirrorOutput` from `core.outputs` and `core`.

## 3. Runtime Integration

- [x] 3.1 Update `MigrationRunner.run()` to call `output.finish(True)` once after the final flush and before writing the `success` manifest.
- [x] 3.2 Call `output.finish(False)` on failure unless `finish(True)` was already called, logging any error it raises and still writing the `failed` manifest.
- [x] 3.3 Add an `output` argument to `ArtifactManager.write_manifest()` and store it under the `output` key.
- [x] 3.4 Pass `output.describe()` from `MigrationContext.write_manifest()` and from both manifest writes in `DefaultMigrationContextFactory`.
- [x] 3.5 Remove `self.file.seek(0, 2)` from `BaseMigration.dump_tables()`.

## 4. Documentation

- [x] 4.1 Update the output section of `docs/arquitetura.md` with `finish()`, `describe()`, `MirrorOutput` and the manifest `output` key.
- [x] 4.2 Update item 2 of `docs/melhorias-futuras.md` to mark phase 2 as done and point to `DirectDatabaseOutput` as the next output.
- [x] 4.3 Mark the completed steps in the recommended order of `docs/melhorias-futuras.md`.

## 5. Verification

- [x] 5.1 Update test fakes that implement the output contract with `finish()` and `describe()`.
- [x] 5.2 Add unit tests for `SqlFileOutput.finish()` and `describe()`.
- [x] 5.3 Add unit tests for `MirrorOutput` ordering, error handling and description.
- [x] 5.4 Add runner tests for success, failure, failing `finish(True)` and failing `finish(False)`.
- [x] 5.5 Add a manifest test for the default `output` description and a `dump_tables()` test with `MirrorOutput`.
- [x] 5.6 Keep the existing inherited migration artifact regression test passing without changing its expected SQL.
- [x] 5.7 Run `python3 -m unittest discover -s tests`.
- [x] 5.8 Run `python3 -m compileall core Acme tests`.
- [x] 5.9 Run `openspec validate introduzir-mirror-output` and `openspec status --change "introduzir-mirror-output"`.
