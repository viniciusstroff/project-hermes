## Why

A refatoração para adapters e a composição por `MigrationContext` deixaram para trás código que ninguém chama. Ele não muda o comportamento da migração, mas confunde quem lê o projeto e quem copia os exemplos da documentação:

- `common/dateutil.py` e `common/regexutil.py` não são importados em lugar nenhum e os dois têm defeito. O `validate()` usa o formato `'%d/$m/%Y'` e nunca aceita uma data. A classe de caracteres de `REGEX_VALID_CHARACTERS` fecha no `]` logo depois do `[`, então a expressão só casa com o texto literal `{}:, ]` precedido de um caractere fora da classe.
- `core/engine.py` reexporta classes que hoje moram em `core/migration_engine.py`, `core/table_migration.py` e outros módulos. Nada no repositório importa `core.engine`.
- O `BaseMigration` tem doze helpers privados que só repassam ao contexto e não têm chamada no repositório. Dois deles, `_engine` e `_fb_engine`, ainda aparecem nos exemplos de `docs/estrategias.md` e `docs/novo-cliente.md`, enquanto o `Acme` usa `self.context.engine('pg_v1')`.
- O `core/base_migration.py` importa `firebirdsql` e `psycopg2` sem usar. Quem abre as conexões é o `core/context.py`. Os testes aplicam `patch` nesses imports e só funcionam porque o módulo importado é o mesmo objeto.
- `queries_duplicidade_dados.sql`, na raiz, tem dois `UPDATE` sobre tabelas fictícias que não existem no `Acme` e não é citado por nenhum script nem documento.
- `.vscode/launch.json` e `.vscode/tasks.json` apontam para o container `migracao` e o caminho `/home/migracao/Acme/Migracao.py`. O container se chama `project-hermes`, monta o projeto em `/home/project-hermes` e o ponto de entrada é `Migracao.py Acme`. Com o `remoteRoot` errado, os breakpoints do VS Code não param.
- O item 1 de `docs/melhorias-futuras.md` ainda apresenta como "possível estrutura" e "contratos sugeridos" uma organização que já existe, com caminhos diferentes dos reais (`core/migrations/` não existe).

## What Changes

- Remover o diretório `common/`.
- Remover `core/engine.py`. As exportações de `core/__init__.py` já apontam para os módulos atuais e ficam como estão.
- **BREAKING:** remover do `BaseMigration` os helpers `_engine`, `_fb_engine`, `_v1_cursor`, `_log_table_progress`, `_setup_tui`, `_teardown_tui`, `_footer_lines`, `_render_progress_footer`, `_reset_progress`, `_finish_progress`, `_log_file` e `_tui_active`. O caminho para obter um engine passa a ser só `self.context.engine(nome)`. O projeto não está em produção, então não fica alias.
- Remover os imports de driver de `core/base_migration.py` e apontar os `patch` dos testes para `core.context`.
- Remover `queries_duplicidade_dados.sql`.
- Corrigir `.vscode/launch.json` e `.vscode/tasks.json` para o container e o caminho atuais.
- Atualizar os exemplos de `docs/estrategias.md` e `docs/novo-cliente.md`, a árvore do `README.md` e os itens 1 e 9 de `docs/melhorias-futuras.md`.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `adapter-agnostic-migration-core`: o core deixa de expor o módulo de compatibilidade `core.engine` e os helpers privados de engine e progresso no `BaseMigration`. Os engines vêm só de `context.engine(nome)`.

## Impact

- Arquivos removidos: `common/dateutil.py`, `common/regexutil.py`, `core/engine.py`, `queries_duplicidade_dados.sql`.
- Código afetado: `core/base_migration.py`, `Migracao.py` (comentário sobre `common/`), `.vscode/launch.json`, `.vscode/tasks.json` e `tests/test_base_migration_artifacts.py`.
- Clientes privados que usam `self._engine`, `self._fb_engine`, `self._v1_cursor()`, `from core.engine import ...` ou `from common...` quebram na próxima execução e precisam trocar pela API atual.
- Os arquivos SQL gerados e o manifesto não mudam.
