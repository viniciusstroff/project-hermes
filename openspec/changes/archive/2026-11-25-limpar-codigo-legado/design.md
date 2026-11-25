## Context

O item 9 de `docs/melhorias-futuras.md` lista quatro frentes: revisar `common/dateutil.py`, revisar imports antigos em `core/engine.py` e `core/__init__.py`, consolidar a documentação e remover helpers duplicados depois da refatoração. A busca no repositório, fora das changes arquivadas, deu este resultado:

| Ponto | Origem | Quem usa hoje |
|---|---|---|
| `common/dateutil.py` | commit inicial `7ef3728` | ninguém. O `README.md` cita `common/` na árvore e o `Migracao.py` num comentário |
| `common/regexutil.py` | commit inicial `7ef3728` | ninguém |
| `core/engine.py` | reduzido a reexportação em `9698a89`, quando o motor foi dividido | ninguém |
| `core/__init__.py` | exportações preguiçosas para os módulos atuais | `tests/test_outputs.py` confere as exportações |
| `BaseMigration._engine` | fachada de compatibilidade | só os exemplos de `docs/estrategias.md` e `docs/novo-cliente.md` |
| `BaseMigration._fb_engine`, `_v1_cursor` e os helpers de progresso e TUI | fachada de compatibilidade | ninguém. O `MigrationRunner` e o `MigrationProgress` usam o contexto direto |
| imports `firebirdsql` e `psycopg2` em `core/base_migration.py` | sobra da conexão que saiu para `core/context.py` | só os `patch` dos testes |
| `queries_duplicidade_dados.sql` | commit inicial, sanitizado em `2bd6aee` | ninguém |
| `.vscode/launch.json` e `.vscode/tasks.json` | nomes anteriores ao container `project-hermes` | o `README.md` e o `docs/execucao.md` mandam usar a configuração `Attach Python in Docker` |

O `__pycache__/` da raiz não é versionado: o `.gitignore` já ignora `**/__pycache__/**`. Não há o que fazer nele.

A regra do projeto é remover a API antiga em vez de manter alias, porque nada está em produção. Os clientes reais ficam em repositórios privados e podem usar o que este repositório não usa.

## Goals / Non-Goals

**Goals:**

- Tirar do repositório os módulos, helpers e arquivos que não têm uso.
- Deixar um caminho só para obter um engine: `context.engine(nome)`.
- Fazer a configuração de depuração do VS Code funcionar com o container atual.
- Separar no roadmap o que já existe do que ainda é proposta.

**Non-Goals:**

- `FieldStrategy.value()`, `sql_value()` e o `RegexClean`: ficam com a change `revisar-estrategias-dependentes-de-dialeto`.
- `load_reference_map()` e `_reference_maps`: ficam com a change `padronizar-lookups-de-referencia`.
- `pg_v1_conn`, `pg_v2_conn` e `fb_v1_conn`: o item 1 do roadmap espera os clientes privados migrarem.
- `rodar2.sh`: a remoção é uma Open Question da change `aplicar-artefatos-sql-no-destino`.
- Os helpers de escrita da fachada (`write_sql()`, `print_comment()`, `_open_sql_file()` e `_flush_writes()`): o spec `configurable-migration-output` exige que continuem e o `docs/arquitetura.md` os documenta.
- O contrato `TARGET_DB_*` do `.env.example` da raiz: é assunto do item 1 (ver Open Questions).
- A reorganização dos testes por arquivo, prevista no item 4.

## Decisions

1. Remover `common/` inteiro.

   Os dois arquivos estão quebrados e sem uso. O que eles tentavam fazer já existe no core: `DateConvert` faz o parse de datas e devolve `NULL` para valor inválido. O `RegexClean` limpa caracteres por expressão regular.

   Alternativa considerada: corrigir o formato e a expressão e manter o pacote. Rejeitada porque não há quem chame. Um utilitário corrigido sem uso continuaria fora dos testes.

2. Remover `core/engine.py` e manter `core/__init__.py`.

   O `core/__init__.py` exporta por import preguiçoso nomes que vêm de `core.migration_engine`, `core.table_migration`, `core.adapters` e outros módulos atuais. Nenhuma entrada aponta para `core.engine`. A revisão pedida no roadmap termina sem mudança nesse arquivo. O `core/engine.py` sai. Quem precisar das classes importa de `core` ou do módulo de origem.

3. Remover os helpers privados sem uso do `BaseMigration`.

   Saem `_engine`, `_fb_engine`, `_v1_cursor`, `_log_table_progress`, `_setup_tui`, `_teardown_tui`, `_footer_lines`, `_render_progress_footer`, `_reset_progress`, `_finish_progress`, `_log_file` e `_tui_active`. Todos só repassam ao contexto. O `_v1_cursor(factory=None)` ainda ignora o argumento `factory`, então nem repassa direito.

   Os exemplos da documentação passam a usar `self.context.engine('pg_v1')`, como o `Acme`. Para o Firebird, `self.context.engine('fb_v1')`.

   Ficam: `_open_sql_file()` e `_flush_writes()`, que são API documentada da fachada. Fica também `_write_manifest()`, usado internamente pelo `dump_tables()`.

   Alternativa considerada: manter `_engine` como atalho, já que a documentação ensinava esse uso. Rejeitada porque mantém dois caminhos para a mesma coisa. Além disso, o prefixo `_` já dizia que o atributo não era API.

4. Tirar os imports de driver de `core/base_migration.py`.

   Quem abre as conexões é o `DefaultMigrationContextFactory`, em `core/context.py`. Os testes em `tests/test_base_migration_artifacts.py` aplicam `patch('core.base_migration.psycopg2.connect')`, que funciona por acaso: o `patch` troca o atributo `connect` do módulo `psycopg2`, o mesmo objeto que `core.context` usa. Os `patch` passam a mirar `core.context.psycopg2.connect` e `core.context.firebirdsql.connect`, que é onde a conexão acontece.

5. Remover `queries_duplicidade_dados.sql`.

   Os dois `UPDATE` acrescentam o id ao nome de registros duplicados em `acme_registry_demo` e `acme_people_demo`, tabelas que o `Acme` não migra. Essa regra é uma decisão de dados de um cliente. Quando um cliente precisar dela, o lugar é o `run_initial()` ou um método `update_*` do script dele, como o `docs/estrategias.md` orienta em "Quando escrever um método manual".

6. Corrigir a configuração do VS Code.

   O `launch.json` passa a usar `remoteRoot` `/home/project-hermes`, o volume montado pelo `docker-compose.yml`. A tarefa do `tasks.json` passa a rodar `docker exec project-hermes ... Migracao.py Acme`, o mesmo comando do `README.md` e do `docs/execucao.md`.

7. Consolidar o roadmap.

   No item 1 de `docs/melhorias-futuras.md`, "Possível estrutura" e "Contratos sugeridos" saem e dão lugar a um parágrafo que aponta para `docs/arquitetura.md`, onde está a estrutura real. "O que precisa mudar" e "Benefício real" continuam, porque ainda descrevem trabalho futuro. O item 9 fica marcado como feito e a linha `common/` sai da árvore do `README.md`.

## Risks / Trade-offs

- Clientes privados podem usar `self._engine`, `self._fb_engine`, `self._v1_cursor()`, `from core.engine import ...` ou `from common...`, porque a documentação ensinava o primeiro. Mitigação: antes da próxima carga, procurar esses nomes nos repositórios privados e trocar por `self.context.engine('pg_v1')`, `self.context.engine('fb_v1')`, `self.context.sources['pg_v1'].cursor()` e imports de `core`.
- Algum script externo pode depender de `queries_duplicidade_dados.sql`. Mitigação: a busca nos repositórios privados inclui o nome do arquivo. O conteúdo continua no histórico do Git.
- A tarefa do VS Code passa a depender do nome `project-hermes`. É o nome fixado em `container_name` no `docker-compose.yml`.

## Migration Plan

1. Remover `common/`, `core/engine.py` e `queries_duplicidade_dados.sql`.
2. Remover os helpers e os imports do `BaseMigration` e ajustar os `patch` dos testes.
3. Corrigir o `.vscode/`.
4. Atualizar a documentação.
5. Rodar os testes e procurar no repositório qualquer referência aos nomes removidos.

Rollback: reverter os commits. Nada muda nos arquivos gerados nem no manifesto.

## Open Questions

- Algum cliente real usa a regra de `queries_duplicidade_dados.sql`? Se usar em mais de um, vale um exemplo em `docs/novo-cliente.md`.
- O `.env.example` da raiz traz `PG_V1_*`, `FB_V1_*` e `PG_V2_*`, mas o `README.md` diz que o `.env` da raiz só é lido pelo Docker Compose, que usa apenas `TARGET_DB_*`. Essas chaves saem agora ou junto com o contrato `TARGET_DB_*` do item 1?
