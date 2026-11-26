## Why

Uma geração longa que falha no meio recomeça do zero. A causa mais provável é operacional: a conexão com a origem cai pelo túnel SSH, o servidor reinicia, alguém interrompe o processo. Numa base grande, perder duas horas de leitura por uma queda nos últimos minutos é o custo que o item 13 de `docs/melhorias-futuras.md` quer evitar.

A `paralelizar-geracao-de-tarefas-independentes` dá a cada tarefa um nome único e junta o resultado dela ao arquivo da fase numa posição conhecida. Com isso, dá para registrar o que já está pronto e retomar a geração na mesma pasta, pulando as tarefas concluídas.

## What Changes

- Novo `checkpoint.json` na raiz da execução. Depois de cada tarefa juntada ao arquivo da fase, ele registra o nome, a fase, as posições de início e fim no arquivo, o sha256 do trecho, a contagem de linhas da origem e um hash do código do método. A gravação é atômica. Um `flock` impede duas retomadas da mesma pasta ao mesmo tempo.
- Nova opção `--resume <timestamp>` no `Migracao.py`. Ela reabre a mesma pasta, confere a impressão digital da execução, corta o arquivo de cada fase depois da última tarefa confirmada e roda os hooks de novo, pulando o que está antes do corte.
- Nova opção `--rerun <nome>`, que força uma tarefa concluída e tudo o que vem depois dela na fase.
- Mudança no código de uma tarefa concluída vira aviso. Mudança na contagem da origem recusa a retomada.
- A retomada aceita só `--output file` nesta change.
- O relatório da geração marca as tarefas retomadas e lista as tentativas.
- O runner grava `failed` e o checkpoint também numa interrupção por Ctrl+C.

## Capabilities

### New Capabilities

- `generation-checkpoints`: checkpoint por tarefa, retomada da geração na mesma pasta, impressão digital da execução, `--rerun`, conferência da origem e limpeza no sucesso.

### Modified Capabilities

- `parallel-generation`: tarefas paralelas que terminaram mas não foram juntadas entram na retomada sem rodar de novo.
- `generation-metrics`: o relatório da geração marca as tarefas retomadas e lista as tentativas.

## Impact

- Novo módulo `core/checkpoint.py` (registro, trava, impressão digital e retomada).
- Código afetado: `core/runner.py`, `core/context.py`, `core/parallel.py`, `core/tasks.py`, `core/outputs/file.py`, `core/artifacts.py`, `core/metrics.py` e `Migracao.py`.
- Depende de `paralelizar-geracao-de-tarefas-independentes`, que precisa ser arquivada antes: os nomes das tarefas e a junção na ordem vêm dela.
- Só o que está em tarefas é retomado. O que fica fora delas (`run_initial()`, `reset_sequences()`, comentários soltos) é gerado de novo, porque é barato.
- A aplicação não muda e continua tudo ou nada. Checkpoint na aplicação fica para outra change, junto com a aplicação paralela que depende dele.
