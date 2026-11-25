## Why

Hoje a migração só entrega o SQL em arquivos. A aplicação no destino acontece depois, pelo `SqlArtifactApplier`, quando a geração inteira terminou. Numa carga longa isso tem dois custos. Um erro no SQL gerado só aparece quando o aplicador chega ao comando, depois de toda a geração. E o `03_load_transformed_data.sql` de um cliente grande ocupa gigabytes em disco para ser lido uma vez.

O item 2 de `docs/melhorias-futuras.md` previa um `DirectDatabaseOutput` que executa os comandos no destino durante a geração. Ele foi adiado porque a geração lia o destino no meio da carga para montar mapas de referência (decisão 1 do design de `aplicar-artefatos-sql-no-destino`). A change `padronizar-lookups-de-referencia` tira essa leitura do meio da carga: os mapas são carregados antes da fase `prepare_target` e o provider recusa leituras depois disso. Com isso o output direto volta a ser viável. Também falta escolher o output pela linha de comando, o que hoje não existe.

## What Changes

- Novo `DirectDatabaseOutput`, em `core/outputs/direct_db.py`. Ele executa cada comando gerado no destino assim que o comando fica completo, numa transação única para a execução inteira.
- Modos `apply` (termina em `COMMIT`) e `dry-run` (termina sempre em `ROLLBACK`) e políticas de erro `stop` e `continue`, com o mesmo comportamento do `SqlArtifactApplier`. A execução de comandos, as regras de comandos pulados e o relatório passam a ser compartilhados entre os dois.
- Os comandos da fase `setup` ficam em memória e só rodam quando a fase `prepare_target` abre, depois que as leituras do destino foram fechadas.
- O contrato `OutputAdapter` ganha `include_file(phase, path)`, para fases cujo conteúdo foi gravado fora do output. O `MigrationRunner` usa esse método para entregar o `02_dump_compatible_tables.sql` entre `prepare_target` e `load_transformed_data`. O `SqlFileOutput` mantém o arquivo como está. O `DirectDatabaseOutput` executa o dump na mesma transação.
- Nova opção `--output sql-file|direct-db|hybrid` no `Migracao.py`. O padrão continua `sql-file`. `hybrid` grava os arquivos e executa no destino pelo `MirrorOutput`. Com `direct-db` ou `hybrid`, `--apply` ou `--dry-run` é obrigatório e define o modo do output. Nesse caso nada é aplicado depois da geração.
- O `DefaultMigrationContextFactory` monta o output escolhido e abre a conexão de escrita com as chaves `PG_V2_*`.
- Atualização de `docs/execucao.md`, `docs/arquitetura.md`, `docs/melhorias-futuras.md` e do `README.md`.

## Capabilities

### New Capabilities

- `direct-database-output`: executa no destino, durante a geração, os comandos entregues ao output, em transação única, com modo, política de erro e relatório.

### Modified Capabilities

- `configurable-migration-output`: o output passa a ser escolhido pelo CLI, o contrato ganha `include_file()` e o runner entrega o dump ao output. Sai o requisito que proibia a camada de output de executar SQL no destino.

## Impact

- Novo módulo `core/outputs/direct_db.py`.
- Código afetado: `core/outputs/base.py`, `core/outputs/sql_file.py`, `core/outputs/mirror.py`, `core/runner.py`, `core/context.py`, `core/base_migration.py` (`dump_tables()` registra o arquivo gerado), `core/metadata/postgres.py`, `core/apply/` (execução compartilhada e separador incremental) e `Migracao.py`.
- Depende de `padronizar-lookups-de-referencia` e só pode ser implementada depois dela.
- Com `sql-file`, nada muda nos arquivos gerados. O `Aplicar.py` e as opções `--apply` e `--dry-run` sem `--output` continuam como estão.
- Na execução direta, o destino fica bloqueado pelo `TRUNCATE` desde a fase `prepare_target` até o fim da geração, não só durante a aplicação. A premissa de destino sem uso concorrente continua.
- Um cliente que lê o destino por `pg_v2_conn` no meio da geração fica fora da garantia e pode esperar o lock da conexão de escrita.
