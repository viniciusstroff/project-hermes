## Why

Hoje o `rodar2.sh` aplica os arquivos no destino com `psql` em autocommit e sem `ON_ERROR_STOP`. Um erro não interrompe a carga e os erros só são filtrados do log depois. Quando algo dá errado, o destino fica carregado pela metade e precisa ser recriado a partir do dump de homologação. Também não há como validar uma execução contra o destino sem alterá-lo.

O item 2 de `docs/melhorias-futuras.md` previa resolver isso com um `DirectDatabaseOutput`, que executaria o SQL no destino durante a geração. Ao detalhar o desenho apareceu um conflito com o fluxo atual: a migração lê o destino enquanto gera os comandos (decisão 1 do design). Esta change entrega a transação única e o dry run aplicando os artefatos depois da geração.

## What Changes

- Novo `SqlArtifactApplier`, que aplica os arquivos de `sql/` de uma execução na ordem das fases, dentro de uma única transação.
- Modo `apply`, que termina em `COMMIT`, e modo `dry-run`, que sempre termina em `ROLLBACK`.
- Política de erro `stop`, que desfaz tudo no primeiro erro, ou `continue`, que registra o erro e segue usando savepoint por lote.
- Separador de comandos que segue as regras léxicas do PostgreSQL. Com ele o aplicador executa um comando por vez e aponta qual falhou.
- Tratamento de três situações que o `psql` resolvia por abrir uma sessão por arquivo: o `BEGIN`/`COMMIT` que o `pg_dump` grava em torno de large objects, os metacomandos do `psql` (como `\restrict`) e as configurações de sessão alteradas pelo dump. Além disso, os `setval` não rodam no dry run.
- Relatório de cada aplicação em `aplicacoes/<timestamp>-<modo>/`, dentro da pasta da execução.
- Novo ponto de entrada `Aplicar.py <run_dir> [--dry-run] [--on-error stop|continue]` e opções `--apply` e `--dry-run` no `Migracao.py`.
- Atualização de `docs/execucao.md`, `docs/arquitetura.md` e do item 2 de `docs/melhorias-futuras.md`.

## Capabilities

### New Capabilities

- `sql-artifact-application`: aplica os artefatos SQL de uma execução no banco de destino em transação única, com dry run e relatório.

### Modified Capabilities

- None. A geração continua sem executar SQL no destino, como exige `configurable-migration-output`.

## Impact

- Novos módulos: `core/apply/` (aplicador, separador de comandos e relatório) e `Aplicar.py`.
- Código afetado: `Migracao.py`.
- O `rodar2.sh` continua disponível. Removê-lo depende de saber onde a aplicação vai rodar (Open Questions do design).
- Sem mudança nos arquivos SQL gerados, no manifesto da geração ou no contrato `OutputAdapter`.
- Durante a transação o destino não pode ter uso concorrente, a mesma premissa da janela de carga atual.
