## Why

O `OutputAdapter` v1 isolou a saída da migração, mas o runtime ainda entrega tudo para um único output e o output nunca fica sabendo se a execução terminou bem. O `DirectDatabaseOutput` previsto no item 2 de `docs/melhorias-futuras.md` depende das duas coisas: rodar junto com o `SqlFileOutput` para manter os arquivos de auditoria e decidir entre `COMMIT` e `ROLLBACK` no fim. Esta change prepara esse terreno sem executar nada no banco.

## What Changes

- Adicionar `finish(success: bool)` ao contrato `OutputAdapter`. O `MigrationRunner` chama esse método uma vez por execução, com `True` depois do último `flush()` ou com `False` quando a execução falha.
- Adicionar `describe()` ao contrato `OutputAdapter`, devolvendo um dicionário serializável que identifica o output.
- Registrar a descrição do output no `manifest.json`, na chave `output`.
- Implementar `MirrorOutput`, que repassa fases, SQL, comentários, flush, finish e close para uma lista de outputs na ordem recebida.
- Implementar `finish()` e `describe()` no `SqlFileOutput` sem mudar os arquivos gerados.
- Remover de `BaseMigration.dump_tables()` o `self.file.seek(0, 2)`, que mexe no `00_setup.sql` e não no arquivo do dump, e que quebraria com qualquer output sem atributo `file`.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `configurable-migration-output`: o contrato passa a receber o resultado da execução e a se descrever no manifesto. Entra o `MirrorOutput` como composição de outputs.

## Impact

- Código afetado: `core/outputs/base.py`, `core/outputs/sql_file.py`, `core/outputs/__init__.py`, `core/__init__.py`, `core/runner.py`, `core/context.py`, `core/artifacts.py`, `core/base_migration.py`.
- Novo módulo: `core/outputs/mirror.py`.
- Outputs feitos fora do core precisam implementar `finish()` e `describe()`. Hoje só os fakes de teste estão nessa situação.
- O `manifest.json` ganha a chave `output`. As demais chaves continuam iguais.
- Sem mudança em `.env`, CLI, nomes de arquivos SQL ou ordem das fases. O padrão continua sendo só `SqlFileOutput`.
