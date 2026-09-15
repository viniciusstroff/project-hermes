## Why

O core de migração ainda depende diretamente de PostgreSQL e Firebird em pontos centrais, o que torna cada troca de origem ou destino uma refatoração transversal. Esta mudança cria contratos explícitos para origem, destino, dialeto e metadados, preparando o projeto para novos bancos ou sinks sem misturar regra de Acme com detalhes de driver.

## What Changes

- Introduzir contratos abstratos para leitura de origem, representação de destino, serialização SQL e operações de metadados.
- Mover os detalhes concretos de `psycopg2`, `firebirdsql`, cursores, `LIMIT`/`ROWS`, quoting e comandos PostgreSQL para adapters e providers dedicados.
- Atualizar o core para depender desses contratos em vez de conexões concretas.
- Manter PostgreSQL V1, Firebird V1 e PostgreSQL V2 como implementações concretas iniciais dos novos contratos.
- Preservar o comportamento atual de geração de arquivos SQL como padrão, sem tentar mudar a camada de saída nesta mudança.
- Preparar os Acmes para usar adapters/helpers do core em vez de acessar `pg_v1_conn`, `pg_v2_conn` e `fb_v1_conn` diretamente.

## Capabilities

### New Capabilities

- `adapter-agnostic-migration-core`: define como o core de migração deve consumir contratos de origem, destino, dialeto e metadados sem depender de drivers ou dialetos concretos.

### Modified Capabilities

- None.

## Impact

- Código afetado: `core/base_migration.py`, `core/migration_engine.py`, `core/table_migration.py`, `core/multi_target_migration.py`, `core/expand_migration.py`, `core/strategies.py` e migrações Acme que acessam conexões diretamente.
- Novos módulos esperados: `core/adapters/`, `core/dialects/` e `core/metadata/`.
- Dependências externas atuais continuam válidas para os adapters existentes; a mudança não exige remover `psycopg2` ou `firebirdsql`.
- A compatibilidade operacional deve ser preservada para o fluxo atual PostgreSQL V1 + Firebird V1 para PostgreSQL V2.
