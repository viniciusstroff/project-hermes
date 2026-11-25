## 1. Fix

- [ ] 1.1 In `core/dialects/postgres.py`, double every backslash inside the `E'...'` literal rendered for strings that contain one.
- [ ] 1.2 Keep strings without backslash rendered as `'...'` with doubled single quotes.

## 2. Tests

- [ ] 2.1 Replace the expectation `sql_value(r'a\b') == r"E'a\b'"` in `tests/test_strategies.py` with `E'a\\b'`.
- [ ] 2.2 Add dialect tests for a trailing backslash, a backslash next to a quote, `\n`, `\x41`, `\101` and a string without backslash.
- [ ] 2.3 Add a splitter test with an INSERT rendered by `PostgresTargetAdapter` for a value ending in backslash, followed by another statement, and assert two statements.
- [ ] 2.4 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core Acme tests`.

## 3. Verification

- [ ] 3.1 Against PostgreSQL 16, select each rendered literal with `standard_conforming_strings` on and off and compare with the original value.
- [ ] 3.2 Generate a run for a client with backslashes in transformed tables and diff its SQL with a previous run: only literals with backslash change.
- [ ] 3.3 Run `openspec validate preservar-barra-invertida-nos-literais`.

## 4. Documentation

- [ ] 4.1 Replace the risk about manual escaping in `docs/arquitetura.md` with the serialization rule for strings with backslash.
- [ ] 4.2 Record in `docs/melhorias-futuras.md` the serialization gaps left out of this change: `bytes`/`memoryview`, `nan`/`inf` and the NUL character.
