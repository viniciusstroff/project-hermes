## Why

O Hermes só migra de PostgreSQL e Firebird para PostgreSQL. O caminho completo também depende de uma versão específica. O `pg_dump` 9.6 fixo no `Dockerfile` recusa servidores mais novos, por isso a V1 precisa ser 9.6 (a AcmeEdu fixa `postgres:9.6-alpine` só por causa disso). Nenhuma camada escolhe o banco pela configuração:

- o `DefaultMigrationContextFactory` sempre conecta `PG_V1_*` e `PG_V2_*` e fixa `PostgresTargetAdapter`, `PostgresMetadataProvider` e `SqlFileOutput`. O `core/context.py` e o `core/base_migration.py` importam `psycopg2` e `firebirdsql` ao carregar. O `TARGET_DB_ADAPTER` do `.env.example` não é lido por nenhum código;
- todos os contratos trafegam SQL: a origem recebe texto SQL (`execute(sql)`, `scalar(sql)`, `limit_clause('LIMIT n')`), o destino devolve `INSERT` em texto, o `MetadataProvider` devolve SQL de triggers, `pg_index` e `setval`; o output recebe `write(sql)`;
- a API do cliente também é SQL: `source_sql` com `{fields}` e `{limit}`, `SqlExpression`, `write_sql`, `TRUNCATE` escrito à mão e conexões DB-API expostas;
- a aplicação (`core/apply`) só funciona com `psycopg2` e com o lexer do PostgreSQL.

O objetivo é que qualquer banco com driver em Python, relacional ou NoSQL, sirva de origem ou de destino desde que exista um adapter, sem mudança no core e sem versão fixa.

## What Changes

- **Conexões nomeadas e registro de adapters.** O `.env` do cliente declara cada conexão pelo nome e pelo adapter (`SOURCE_V1_ADAPTER=postgres`, `TARGET_ADAPTER=mongodb`), mais os parâmetros do adapter. Um registro resolve o nome para o adapter e importa o driver só quando ele é usado. Um adapter de fora do core entra por caminho (`pacote.modulo:Classe`).
- **Leitura neutra.** A origem recebe `SourceQuery` (entidade, campos, filtro de igualdade, ordenação e limite) ou `NativeQuery` (consulta na linguagem do banco, com o adapter declarado). O contrato passa a ser `read(query)` e `count(query)`.
- **Escrita neutra.** A migração emite operações (`Insert`, `Clear`, `SuspendConstraints`, `ResumeConstraints`, `ResetIdentity`, `Native`, `Comment`) e o adapter de destino traduz cada uma para o formato dele. A operação que não faz sentido num banco não gera nada nele.
- **Artefatos no formato do destino.** Cada fase vira `comandos/<fase>.<extensão>`, com `.sql` no PostgreSQL e `.jsonl` no MongoDB. O manifesto registra adapter, versão do servidor e formato de cada artefato.
- **Cópia de entidades compatíveis pelos adapters.** A fase do `pg_dump` vira uma cópia genérica das entidades listadas pelo cliente. O `pg_dump` fica como atalho opcional do PostgreSQL para PostgreSQL, com conferência de versão.
- **Aplicação guiada por capacidades.** O aplicador lê cada artefato pelo leitor do formato e executa pela sessão do adapter. O adapter declara se tem transação, savepoint e desligamento de restrições. O aplicador recusa o que o destino não consegue cumprir. O comportamento atual (lexer, savepoints, `RESET ALL`, comandos pulados) vai para dentro do adapter PostgreSQL.
- **Versão detectada, não fixada.** Cada adapter lê a versão do servidor ao conectar, grava no manifesto e recusa versões fora da faixa que declara. O `Dockerfile` deixa de fixar o cliente do PostgreSQL e cada driver ganha um arquivo de requisitos próprio.
- **Adapter MongoDB** nos dois lados, com `pymongo`, como prova de que os contratos servem para NoSQL.
- **AcmeEdu neutra** e uma matriz de ponta a ponta: PostgreSQL 9.6 → 16, PostgreSQL 17 → 17, PostgreSQL → MongoDB, MongoDB → PostgreSQL e Firebird 3 e 5 → PostgreSQL.
- **BREAKING:** saem as chaves `PG_V1_*`, `PG_V2_*` e `FB_V1_*`, os nomes fixos `pg_v1` e `fb_v1`, `source_sql`, `write_sql`, `print_comment`, `sql_value`, o `MetadataProvider`, os atributos `pg_v1_conn`, `pg_v2_conn` e `fb_v1_conn`, a pasta `sql/`, `dump_ignoring_tables()` e `dump_non_existing_tables()`. O `Aplicar.py` deixa de ler as variáveis da libpq e passa a ler `TARGET_*`. O projeto não está em produção, então não ficam aliases.

## Capabilities

### New Capabilities

- `database-adapters`: conexões nomeadas, registro de adapters, carga do driver sob demanda, capacidades, detecção de versão com faixa suportada, cópia de entidades compatíveis e os adapters PostgreSQL, Firebird e MongoDB.

### Modified Capabilities

- `adapter-agnostic-migration-core`: a origem é lida por consultas neutras ou nativas declaradas, o destino recebe operações e a manutenção do destino deixa de ser SQL devolvido por um provider.
- `configurable-migration-output`: o output recebe operações e grava artefatos no formato do adapter de destino. Saem os requisitos presos a arquivos `.sql` e ao `pg_dump`.
- `sql-artifact-application`: a aplicação deixa de ser só de SQL e passa a seguir as capacidades do adapter de destino.

## Impact

- Código afetado: praticamente todo o `core/` (`adapters/`, `dialects/`, `metadata/`, `outputs/`, `apply/`, `context.py`, `runner.py`, `artifacts.py`, `base_migration.py`, `table_migration.py`, `expand_migration.py`, `multi_target_migration.py`, `migration_engine.py`), `Migracao.py`, `Aplicar.py`, `Dockerfile`, `requirements.txt`, `.env.example`, `Acme/` e `AcmeEdu/`.
- Novo módulo `core/adapters/mongodb.py` e nova dependência opcional `pymongo`.
- Depende das changes `revisar-estrategias-dependentes-de-dialeto` (strategies só com valores Python) e `padronizar-lookups-de-referencia` (mapas declarados, sem SQL livre), que estão no branch `doc/propostas-melhorias-futuras`.
- A change `serializar-tipos-especiais-no-dialeto` mexe no `PostgresDialect`, que passa a ser usado só pelo adapter PostgreSQL. As duas podem entrar em qualquer ordem.
- A change `introduzir-direct-database-output` precisa ser revista antes de ser implementada: o output direto passa a executar operações pela sessão do adapter, não SQL.
- As changes `aplicar-artefatos-sql-no-destino`, `preservar-barra-invertida-nos-literais` e `criar-cliente-ficticio-educacional` precisam ser arquivadas antes, para que as capabilities modificadas aqui existam em `openspec/specs/`.
- Os clientes privados precisam ser reescritos para a nova API e o novo `.env`. O `rodar2.sh` continua servindo só para artefatos `.sql`.
