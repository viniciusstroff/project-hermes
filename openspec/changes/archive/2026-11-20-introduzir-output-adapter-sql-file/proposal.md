## Why

O runtime de migração já foi separado por adapters de origem/destino, mas a forma de saída ainda está acoplada ao writer de arquivos SQL. Isso impede evoluir para execução direta, saída espelhada ou pipelines externos sem voltar a alterar o fluxo principal da migração.

## What Changes

- Introduzir um contrato `OutputAdapter` para abertura de fases, escrita, flush e fechamento de saída.
- Transformar o comportamento atual de geração de arquivos SQL no adapter concreto padrão `SqlFileOutput`.
- Atualizar `MigrationContext`, `MigrationRunner` e a fachada `BaseMigration` para dependerem do contrato de saída em vez de um writer específico.
- Manter o comportamento externo atual como padrão: mesmos artefatos, nomes de arquivos SQL, buffering e manifesto.
- Remover `context.writer` e `SqlArtifactWriter`. BREAKING para código composto que usava o alias, aceito porque o projeto ainda não está em produção.
- Documentar que `direct-db`, `hybrid`, `stdout` e outros outputs continuam fora do escopo desta change.

## Capabilities

### New Capabilities

- `configurable-migration-output`: define como o runtime de migração deve entregar comandos por meio de um adapter de saída, preservando `SqlFileOutput` como implementação padrão.

### Modified Capabilities

- None.

## Impact

- Código afetado: `core/context.py`, `core/runtime.py`, `core/runner.py`, `core/base_migration.py`, exports do pacote `core` e testes relacionados a artefatos.
- Novo módulo esperado: `core/outputs/`.
- Não há mudança esperada nas variáveis `.env`, no CLI, nos nomes dos arquivos gerados ou no fluxo operacional padrão.
- Esta change prepara extensões futuras de saída, mas não implementa execução direta no banco.
