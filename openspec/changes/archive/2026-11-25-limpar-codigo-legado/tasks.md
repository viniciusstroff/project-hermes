## 1. Unused Modules and Files

- [x] 1.1 Remove `common/dateutil.py`, `common/regexutil.py` and the `common/` directory, and drop the `common/` mention from the path comment in `Migracao.py`.
- [x] 1.2 Remove `core/engine.py`, keeping `core/__init__.py` unchanged.
- [x] 1.3 Remove `queries_duplicidade_dados.sql`.

## 2. BaseMigration Facade

- [x] 2.1 Remove `_engine`, `_fb_engine`, `_v1_cursor`, `_log_table_progress`, `_setup_tui`, `_teardown_tui`, `_footer_lines`, `_render_progress_footer`, `_reset_progress`, `_finish_progress`, `_log_file` and `_tui_active` from `core/base_migration.py`.
- [x] 2.2 Remove the unused `firebirdsql` and `psycopg2` imports from `core/base_migration.py`.
- [x] 2.3 Change the patches in `tests/test_base_migration_artifacts.py` from `core.base_migration.psycopg2.connect` and `core.base_migration.firebirdsql.connect` to `core.context.psycopg2.connect` and `core.context.firebirdsql.connect`.

## 3. Editor Configuration

- [x] 3.1 Set `remoteRoot` to `/home/project-hermes` in `.vscode/launch.json`.
- [x] 3.2 Make the debug task in `.vscode/tasks.json` run `docker exec project-hermes ... Migracao.py Acme`, matching `README.md` and `docs/execucao.md`.
- [x] 3.3 Replace `pkill`, which the `python:3.12-slim` image does not have, with a PID file in `/tmp/debugpy.pid`, so a new debug session ends the previous one and frees port 5678.

## 4. Documentation

- [x] 4.1 Replace `.run(self._engine)` with `.run(self.context.engine('pg_v1'))` in `docs/estrategias.md` and `docs/novo-cliente.md`.
- [x] 4.2 Remove the `common/` line from the tree in `README.md`.
- [x] 4.3 In item 1 of `docs/melhorias-futuras.md`, replace "Possível estrutura" and "Contratos sugeridos" with a pointer to `docs/arquitetura.md`, keeping "O que precisa mudar" and "Benefício real".
- [x] 4.4 Mark item 9 of `docs/melhorias-futuras.md` as implemented, naming what was removed and what stayed out of scope.

## 5. Verification

- [x] 5.1 Add tests that `core.engine` cannot be imported, that every name in `core.__all__` resolves and that `BaseMigration` does not expose the removed helpers.
- [x] 5.2 Search the repository, outside archived changes, for `_engine`, `_fb_engine`, `_v1_cursor`, `core.engine`, `common.` and `queries_duplicidade_dados` and confirm no reference remains.
- [x] 5.3 Attach the VS Code debugger to `Migracao.py Acme` in the `project-hermes` container and confirm a breakpoint stops. Done in 2026-11-25 with a minimal DAP client that sends the `launch.json` path mappings: the breakpoint at `Migracao.py:112` was verified and hit, and running the task twice left one debuggee.
- [x] 5.4 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core Acme tests Migracao.py Aplicar.py`.
- [x] 5.5 Run `openspec validate limpar-codigo-legado --strict`.
