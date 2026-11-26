## Why

Hoje a migração entrega as operações em arquivos, em `comandos/`. A aplicação no destino acontece depois, pelo `ArtifactApplier`, quando a geração inteira terminou. Numa carga longa isso tem dois custos. Um erro num comando só aparece quando o aplicador chega a ele, depois de toda a geração. E o `03_load_transformed_data` de um cliente grande ocupa gigabytes em disco para ser lido uma vez.

O item 2 de `docs/melhorias-futuras.md` previa um `DirectDatabaseOutput` que executa os comandos no destino durante a geração. Ele foi adiado porque a geração lia o destino no meio da carga. A `padronizar-lookups-de-referencia` resolveu isso: os mapas de referência carregam antes da primeira fase e as leituras do destino fecham em seguida.

A `desacoplar-migracao-de-banco-e-versao` deixou o output direto pequeno. O output recebe operações completas, não pedaços de texto. A sessão do adapter de destino (`TargetSession`) já sabe ler comandos, pular os que não executa, abrir transação e savepoint e dar o código de um erro. As capacidades dizem o que o destino cumpre. O output direto não precisa de separador, de regras de comandos pulados nem de driver próprios: ele liga as operações à mesma sessão que o aplicador usa. Falta escolher o output pela linha de comando.

## What Changes

- Novo `DirectDatabaseOutput`, em `core/outputs/direct.py`. Ele pede ao adapter de destino os comandos de cada operação (`render`), passa o texto pela sessão do adapter e entrega os comandos à sessão antes de o `write()` retornar, numa transação única para a execução inteira. Uma sessão que junta comandos em lote (MongoDB) roda o lote quando ele enche, na troca de fase ou no fim.
- A sessão de escrita é aberta numa conexão própria, só quando a fase `prepare_target` abre, depois de as leituras do destino fecharem. Os comandos da fase `setup` esperam na memória até lá.
- Modos (`apply` e `dry-run`) e políticas de erro (`stop` e `continue`) saem do `applier.py` para `core/apply/execution.py`, usados pelo aplicador e pelo output direto. A conferência de capacidades continua em `core/application.py`.
- O `TargetSession.commands()` passa a ler linhas, não um caminho: o aplicador passa o arquivo aberto e o output direto passa o texto renderizado de cada operação.
- O `session()` do adapter de destino ganha `waits_between_commands=False`. O output direto pede `True`, porque a sessão espera o Python entre um comando e outro. Com isso, o PostgreSQL desliga o `idle_in_transaction_session_timeout` da sessão.
- O `include_file(path)` do contrato, que já existe para a cópia nativa, executa o arquivo do `pg_dump` na mesma transação.
- Nova opção `--output file|direct|hybrid` no `Migracao.py`. O padrão continua `file`. `hybrid` grava os arquivos e executa no destino pelo `MirrorOutput`. Com `direct` ou `hybrid`, `--apply` ou `--dry-run` é obrigatório e define o modo do output. Nesse caso nada é aplicado depois da geração.
- O `DefaultMigrationContextFactory` monta o output escolhido.
- Atualização de `docs/execucao.md`, `docs/arquitetura.md`, `docs/melhorias-futuras.md` e do `README.md`.

## Capabilities

### New Capabilities

- `direct-database-output`: executa no destino, durante a geração, os comandos das operações entregues ao output, pela sessão do adapter de destino, em transação única, com modo, política de erro, capacidades e relatório.

### Modified Capabilities

- `configurable-migration-output`: o output passa a ser escolhido pelo CLI. Sai o requisito que proibia a camada de output de executar comandos no destino.
- `database-adapters`: a sessão do adapter de destino lê comandos de qualquer sequência de linhas e aceita esperar entre um comando e outro.
- `sql-artifact-application`: o relatório de aplicação registra de onde vieram os comandos (`source`).

## Impact

- Novos módulos: `core/outputs/direct.py` e `core/apply/execution.py`.
- Código afetado: `core/application.py` (`commands()` sobre linhas), `core/apply/applier.py`, `core/apply/cli.py`, `core/apply/report.py`, `core/adapters/postgres.py`, `core/adapters/postgres_session.py`, `core/adapters/mongodb.py`, `core/context.py` e `Migracao.py`.
- Depende de `desacoplar-migracao-de-banco-e-versao`, que precisa ser arquivada antes, porque os deltas desta change partem das specs que ela cria.
- Com `--output file`, nada muda nos arquivos gerados. O `Aplicar.py` e as opções `--apply` e `--dry-run` sem `--output` continuam como estão.
- Na execução direta, o destino fica bloqueado pelo `TRUNCATE` desde a fase `prepare_target` até o fim da geração, não só durante a aplicação. A premissa de destino sem uso concorrente continua.
- Um cliente que lê o destino direto pela conexão do adapter (`context.target.connection`) no meio da geração fica fora da garantia e pode esperar o lock da conexão de escrita.
- Num destino MongoDB, a transação tem um limite de vida (`transactionLifetimeLimitSeconds`) que passa a cobrir a geração inteira, não só a aplicação.
