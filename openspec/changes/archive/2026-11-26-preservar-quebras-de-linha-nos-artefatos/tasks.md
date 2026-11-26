## 1. Artifacts

- [x] 1.1 Open the phase files of `FileOutput` with `newline=''` and copy the included file in `FileOutput.include_file()` without translation.
- [x] 1.2 Open artifacts with `newline=''` in `ArtifactApplier` and in `split_sql_file()`.
- [x] 1.3 Open the included file with `newline=''` in `DirectDatabaseOutput.include_file()`.
- [x] 1.4 Check that the PostgreSQL splitter ends a `--` comment at `\r` and keeps a lone `\r` inside a literal, and fix it if it does not.

## 2. Verification

- [x] 2.1 Add tests: text values with `\r\n`, a lone `\r` and `\n` go from `FileOutput` through `ArtifactApplier` to a fake connection unchanged.
- [x] 2.2 Add tests for `include_file()` of a file with `\r` in `FileOutput` and in `DirectDatabaseOutput`, and check that `hybrid` and the applier still run the same commands.
- [x] 2.3 Add a student note with `\r\n` to the AcmeEdu V1 fixture and a check in `AcmeEdu/conferir.py` that it reaches V2 unchanged, in PostgreSQL and in MongoDB.
- [x] 2.4 Run `python3 -m unittest discover -s tests` and `openspec validate preservar-quebras-de-linha-nos-artefatos --strict`.
