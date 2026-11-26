## Why

A geração roda as unidades de uma migração uma depois da outra, num processo e com uma conexão por origem. Numa base grande, cada tabela espera a anterior mesmo quando não depende dela. Na AcmeEdu, as seis cargas do `run_inserts()` são independentes: o que uma lê vem da origem, não do que outra gerou. As restrições do destino ficam suspensas durante a carga.

O item 12 de `docs/melhorias-futuras.md` pede paralelismo controlado, que mantenha a ordem, as dependências e a auditoria explícitas. O `relatorio-geracao.json` já mostra o tempo de cada unidade. Falta rodar juntas as que não dependem umas das outras.

## What Changes

- Novo decorator `@task(nome, sequence=1)`, em `core/tasks.py`, para os métodos do script do cliente. O nome é opcional (o padrão é o nome do método) e precisa ser único na classe. Tarefas da mesma sequência podem rodar juntas. Uma sequência maior só começa quando as menores terminaram.
- O `run_inserts()` padrão do `BaseMigration` roda as tarefas declaradas. Um cliente que sobrescreve `run_inserts()` chama `self.run_tasks()` onde quiser.
- Opções `--workers N` (padrão 1) e `--worker-mode thread|process` (padrão `thread`) no `Migracao.py`. Com 1 worker, a geração roda em sequência como hoje.
- Cada tarefa roda num contexto próprio, com conexões de origem próprias. Ela grava num segmento. O processo principal junta os segmentos na ordem declarada, pelo `include_file()` do output. O arquivo da fase sai igual ao da geração em sequência.
- Modo processo por `fork`, só em Linux e recusado quando algum adapter é MongoDB.
- Só o processo principal escreve log, progresso e métricas. Os workers mandam registros para ele.
- As entidades compatíveis viram tarefas automáticas (`copy:<entidade>`).
- O manifesto e o `relatorio-geracao.json` registram o paralelismo e cada tarefa.
- A AcmeEdu passa a declarar as seis cargas como tarefas.

## Capabilities

### New Capabilities

- `parallel-generation`: declaração de tarefas por decorator, execução em threads ou processos, contexto da tarefa, junção ordenada dos segmentos, cópia compatível em tarefas e falha de tarefa.

### Modified Capabilities

- `generation-metrics`: o relatório da geração registra o paralelismo e as tarefas.

## Impact

- Novos módulos: `core/tasks.py` (decorator, descoberta e ordem das tarefas) e `core/parallel.py` (contexto da tarefa, segmentos, executores e junção).
- Código afetado: `core/base_migration.py`, `core/context.py`, `core/runner.py`, `core/compatible.py`, `core/migration_engine.py`, `core/metrics.py`, `core/runtime.py`, `core/adapters/sql_source.py`, `core/adapters/mongodb.py`, `Migracao.py` e `AcmeEdu/AcmeEdu.py`.
- Depende de `preservar-quebras-de-linha-nos-artefatos`, porque a junção dos segmentos passa pelo `include_file()`.
- A aplicação não muda e continua numa transação. Aplicação paralela fica para depois de existir checkpoint na aplicação.
- Cada worker abre uma conexão com cada origem que usa. Com N workers, a origem recebe até N conexões a mais.
- Um cliente sem tarefas declaradas roda como hoje, em sequência.
