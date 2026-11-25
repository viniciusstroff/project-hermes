## 1. Unused Modules and Files

- [ ] 1.1 Remove `common/dateutil.py`, `common/regexutil.py` and the `common/` directory, and drop the `common/` mention from the path comment in `Migracao.py`.
- [ ] 1.2 Remove `core/engine.py`, keeping `core/__init__.py` unchanged.
- [ ] 1.3 Remove `queries_duplicidade_dados.sql`.

## 2. BaseMigration Facade

- [ ] 2.1 Remove `_engine`, `_fb_engine`, `_v1_cursor`, `_log_table_progress`, `_setup_tui`, `_teardown_tui`, `_footer_lines`, `_render_progress_footer`, `_reset_progress`, `_finish_progress`, `_log_file` and `_tui_active` from `core/base_migration.py`.
- [ ] 2.2 Remove the unused `firebirdsql` and `psycopg2` imports from `core/base_migration.py`.
- [ ] 2.3 Change the patches in `tests/test_base_migration_artifacts.py` from `core.base_migration.psycopg2.connect` and `core.base_migration.firebirdsql.connect` to `core.context.psycopg2.connect` and `core.context.firebirdsql.connect`.

## 3. Editor Configuration

- [ ] 3.1 Set `remoteRoot` to `/home/project-hermes` in `.vscode/launch.json`.
- [ ] 3.2 Make the debug task in `.vscode/tasks.json` run `docker exec project-hermes ... Migracao.py Acme`, matching `README.md` and `docs/execucao.md`.

## 4. Documentation

- [ ] 4.1 Replace `.run(self._engine)` with `.run(self.context.engine('pg_v1'))` in `docs/estrategias.md` and `docs/novo-cliente.md`.
- [ ] 4.2 Remove the `common/` line from the tree in `README.md`.
- [ ] 4.3 In item 1 of `docs/melhorias-futuras.md`, replace "Possível estrutura" and "Contratos sugeridos" with a pointer to `docs/arquitetura.md`, keeping "O que precisa mudar" and "Benefício real".
- [ ] 4.4 Mark item 9 of `docs/melhorias-futuras.md` as implemented, naming what was removed and what stayed out of scope.

## 5. Verification

- [ ] 5.1 Add tests that `core.engine` cannot be imported, that every name in `core.__all__` resolves and that `BaseMigration` does not expose the removed helpers.
- [ ] 5.2 Search the repository, outside archived changes, for `_engine`, `_fb_engine`, `_v1_cursor`, `core.engine`, `common.` and `queries_duplicidade_dados` and confirm no reference remains.
- [ ] 5.3 Attach the VS Code debugger to `Migracao.py Acme` in the `project-hermes` container and confirm a breakpoint stops.
- [ ] 5.4 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core Acme tests Migracao.py Aplicar.py`.
- [ ] 5.5 Run `openspec validate limpar-codigo-legado --strict`.
