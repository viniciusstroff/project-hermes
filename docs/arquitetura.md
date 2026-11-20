# Arquitetura da Migração Acme

## Visão geral

O script `Migracao.py` é uma aplicação CLI que:

1. Lê a configuração do Acme em `Acme/.env`.
2. Abre conexão com três bancos.
3. Gera arquivos `.sql` com `DELETE`, `UPDATE`, `INSERT`, `ALTER TABLE` e `setval`.
4. Não aplica os dados diretamente na V2.
5. Delega a execução final para `psql`, usando os arquivos gerados em `runs/<cliente>/<timestamp>/sql/`.

## Bancos envolvidos

O script usa três conexões independentes:

- `PG_V1_*`: PostgreSQL da base legada V1.
- `PG_V2_*`: PostgreSQL da base destino V2.
- `FB_V1_*`: Firebird legado, usado para complementar parte das informações migradas.

O contrato novo de ambiente para o destino usa `TARGET_DB_*` e o hostname Docker
`target-db`. As variáveis `PG_V2_*` ainda são necessárias porque o core atual
abre conexões PostgreSQL diretamente; elas devem espelhar `TARGET_DB_*` até a
introdução dos adapters.

Resumo do papel de cada origem:

- `PG_V1`: principal fonte dos `SELECT`s e do `pg_dump`.
- `PG_V2`: fonte de metadados do ambiente destino, especialmente para listar tabelas e gerar comandos de trigger.
- `Firebird V1`: fonte auxiliar para algumas rotinas específicas do Acme.

## Estrutura lógica do script

O script de cada cliente declara a migração, mas o runtime compartilhado é
montado por composição. A classe `BaseMigration` continua disponível para
compatibilidade, porém delega as responsabilidades de infraestrutura para
componentes menores.

Componentes principais:

- `MigrationContext`: agrupa configuração, artefatos, adapters de origem,
  adapter de destino, provider de metadados, output, logger e progresso.
- `DefaultMigrationContextFactory`: cria o contexto padrão a partir das chaves
  `.env` atuais (`PG_V1_*`, `PG_V2_*`, `FB_V1_*`).
- `MigrationRunner`: orquestra as fases padrão da execução.
- `SourceAdapter`: lê linhas e escalares da origem, incluindo diferenças como
  `LIMIT` no PostgreSQL e `ROWS` no Firebird.
- `TargetAdapter` e `Dialect`: renderizam `INSERT`s e valores SQL para o destino.
- `MetadataProvider`: concentra operações auxiliares do destino, como triggers,
  índices, mapas de referência e sequences.
- `OutputAdapter`: recebe o SQL gerado, fase por fase. A implementação padrão é
  `SqlFileOutput`, que grava os arquivos em `runs/<cliente>/<timestamp>/sql/`.
- `BaseMigration`: fachada transitória para scripts que ainda usam herança.

Responsabilidades do cliente:

- listar tabelas ignoradas ou ausentes no `pg_dump`;
- declarar SQL bruto específico do cliente quando necessário;
- declarar migrações de tabelas, transformações, inserts e updates;
- indicar índices ou sequences por meio dos helpers do core quando a operação
  for recorrente.

SQL PostgreSQL puro continua permitido no cliente como escape hatch. A regra
prática é: lógica excepcional do cliente pode ficar no Acme; operações
recorrentes de infraestrutura devem passar por adapter, dialect ou provider.

## API de composição e compatibilidade

O caminho preferido para novas integrações é receber um `MigrationContext` por
composição e usar `context.engine('pg_v1')`, `context.engine('fb_v1')`,
`context.output` e `context.metadata`. Para escrever SQL fora de um engine, use
`context.output.write(...)` e `context.output.comment(...)`. O `BaseMigration`
usa esse mesmo contexto por baixo, então os scripts antigos podem continuar
herdando da base durante a transição.

O contexto expõe a saída só por `context.output`. O antigo `context.writer` e a
classe `SqlArtifactWriter` foram removidos. Quem monta um `MigrationContext` à
mão passa o output no argumento `output=`.

Na fachada por herança, `BaseMigration.write_sql()`, `print_comment()`,
`_open_sql_file()` e `_flush_writes()` continuam disponíveis e delegam para
`context.output`, então funcionam com qualquer `OutputAdapter`. A propriedade
`file` devolve o arquivo aberto pelo `SqlFileOutput` ou `None` quando o output
não grava em arquivo.

Os atributos `pg_v1_conn`, `pg_v2_conn` e `fb_v1_conn` permanecem disponíveis
na fachada por compatibilidade. Código novo deve preferir adapters, providers e
helpers como `load_reference_map()`, `set_index_commands_for()` e
`write_reset_sequences()`.

## Saída da migração

O runtime não abre arquivos SQL por conta própria. Ele entrega os comandos para
um `OutputAdapter`, definido em `core/outputs/base.py`:

```python
class OutputAdapter(Protocol):
    def open_phase(self, phase: str) -> None: ...
    def write(self, sql: str) -> None: ...
    def comment(self, text: str) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

As fases são chaves lógicas: `setup`, `prepare_target`, `load_transformed_data`
e `finalize_target`. A `DefaultMigrationContextFactory` abre `setup` ao montar o
contexto. O `MigrationRunner` abre as outras três na ordem padrão, escreve os
comandos finais de índices e triggers e chama `flush()`. O `close()` acontece em
`MigrationContext.close()`. Os engines criados por `context.engine()` escrevem
pelo mesmo `output.write`.

A implementação padrão é `SqlFileOutput`, em `core/outputs/sql_file.py`. Ela
resolve cada fase pelo `ArtifactManager.sql_path()`, então os nomes de arquivo
continuam definidos em um só lugar. O SQL fica em um buffer de até 500 trechos
antes de ir para o arquivo. Trocar de fase, chamar `flush()` ou `close()` grava
o que estiver pendente. O `comment()` gera o mesmo bloco `-----` que já aparecia
nos arquivos.

O output padrão não executa nada no banco de destino. O próximo output previsto
é o `MirrorOutput`, que encaminha os comandos para mais de um destino. A
execução direta no banco vem depois, com modo de dry run. Os dois estão
descritos no item 2 de [melhorias-futuras.md](melhorias-futuras.md).

## Artefatos gerados

Os artefatos ficam em `runs/<cliente>/<YYYYmmdd-HHMMSS>/`:

- `manifest.json`
- `logs/migration.log`
- `logs/*.sql.log`
- `erros/*.txt`
- `sql/00_setup.sql`
- `sql/01_prepare_target.sql`
- `sql/02_dump_compatible_tables.sql`
- `sql/03_load_transformed_data.sql`
- `sql/04_finalize_target.sql`

Responsabilidade de cada artefato:

- `00_setup.sql`: arquivo inicial da execução.
- `01_prepare_target.sql`: preparação da V2, com desabilitação de triggers/índices e limpeza de dados.
- `02_dump_compatible_tables.sql`: saída do `pg_dump` da V1 para tabelas que podem ser carregadas em bloco.
- `03_load_transformed_data.sql`: carga principal e ajustes intermediários.
- `04_finalize_target.sql`: `setval` de sequences e reativação de índices/triggers.

## Estratégia de carga

O processo mistura dois modelos:

- `pg_dump --column-inserts` para copiar tabelas que podem ser migradas quase como espelho.
- `INSERT INTO ... VALUES ...` gerado em Python para tabelas que exigem mapeamento, filtros, replace, defaults ou correções.

Isso existe porque parte da base V1 não é compatível diretamente com a estrutura da V2.

## Tratamento de incompatibilidades

O script já incorpora decisões de compatibilidade:

- exclusão de tabelas problemáticas no `pg_dump`;
- exclusão de tabelas inexistentes no ambiente do Acme;
- limpeza prévia de tabelas da V2 para evitar `duplicate key`;
- recriação de índices únicos em tabelas fictícias `acme_*`;
- correções posteriores, como prefixo de `document_files.f_file` e ajustes em datas;
- atualização de usernames com encoding inválido em IDs específicos.

## Dependências operacionais

Para funcionar, o ambiente precisa ter:

- `python3`
- `psycopg2`
- `firebirdsql`
- `python-dotenv`
- `pg_dump`
- acesso de rede aos bancos configurados

O diretório `runs/` é criado automaticamente na raiz do projeto e ignorado pelo Git.

## Riscos arquiteturais atuais

- O `main` chama `enable_debug(100)`, o que limita as consultas e impede migração completa se isso não for removido.
- O script gera SQL em arquivo, mas não executa transações fim a fim dentro do Python.
- O escaping é manual em vários `INSERT`s; isso funciona para o padrão atual, mas é frágil para casos fora da curva.
- A lista de tabelas ignoradas e deletadas é estática e depende do histórico do Acme.
