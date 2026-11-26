## Context

A geração de hoje:

- O `MigrationRunner` abre as fases pelo contexto e chama os hooks do cliente. No `run_inserts()`, o cliente chama métodos como `insert_courses()`. Cada um roda unidades: `TableMigration(...).run(self.context.source('v1'))`.
- O `context.source(nome)` devolve um `MigrationEngine` sobre o adapter de origem conectado. O engine emite `Insert` pelo `context.emit`, que entrega ao output da fase.
- O output grava um arquivo por fase (`FileOutput`), executa no destino (`DirectDatabaseOutput`) ou faz os dois (`MirrorOutput`). Todos têm `include_file(path)`, que a cópia nativa já usa: o `pg_dump` grava um arquivo temporário, o output inclui e o arquivo é apagado.
- O `GenerationMetrics` guarda as unidades numa pilha. O `MigrationLogger` e o `MigrationProgress` escrevem no terminal e nos arquivos de log.
- As conexões de origem são do driver de cada banco. Uma conexão do psycopg2 serializa os cursores. O `firebirdsql` não é seguro entre threads.
- A imagem usa Python 3.12, com GIL. Estratégias, renderização e a decodificação do `firebirdsql` são Python puro e seguram o GIL. A espera de rede libera.

Na AcmeEdu, os métodos `insert_*` não dependem do resultado uns dos outros. O `insert_teachers()` lê os alunos da origem para achar a pessoa pelo CPF, mas não lê o que o `insert_students()` gerou.

## Goals / Non-Goals

**Goals:**

- Rodar juntas, na geração, as tarefas que o cliente declarou independentes.
- Manter os artefatos iguais aos da geração em sequência, para a mesma ordem declarada.
- Oferecer threads e processos, escolhidos na linha de comando.
- Deixar o comportamento atual intacto com 1 worker.

**Non-Goals:**

- Não paralelizar a aplicação. Ela continua numa transação. A aplicação paralela depende de checkpoint na aplicação.
- Não compartilhar um snapshot da origem entre workers. A geração em sequência também não tem um. A premissa continua sendo a origem parada durante a janela.
- Não oferecer o modo `spawn`. Ele exige serializar as closures e lambdas dos scripts, o que eles não suportam.
- Não paralelizar dentro de uma tarefa, por faixa de chave ou partição.

## Decisions

1. Tarefas por decorator.

   ```python
   from core.tasks import task

   class Migracao(BaseMigration):
       @task('courses')
       def insert_courses(self): ...

       @task('students', sequence=2)
       def insert_students(self): ...

       @task('teachers', sequence=2)
       def insert_teachers(self): ...
   ```

   O nome é opcional (o padrão é o nome do método) e precisa ser único na classe. A sequência é opcional, com 1 como padrão. Tarefas da mesma sequência podem rodar juntas. Uma sequência maior só começa quando todas as menores terminaram: é assim que o cliente diz que uma tarefa depende do resultado de outra. A ordem de junção segue a sequência. Dentro dela, segue a ordem de declaração na classe, guardada por um contador no momento em que o decorator roda.

   O `run_inserts()` padrão do `BaseMigration` chama `self.run_tasks()`. Um cliente que sobrescreve `run_inserts()` chama `self.run_tasks()` onde quiser. Um cliente sem tarefas roda como hoje.

   Alternativa considerada: um bloco `with self.parallel():` que transformaria em tarefa cada unidade chamada dentro dele. Rejeitada. Ela obrigaria a interceptar o `run()` das unidades, guardar as operações emitidas entre unidades para devolvê-las na ordem e ler a origem no processo principal para consumir no worker. Com o decorator, a tarefa é o método inteiro, com os comentários, as leituras e as unidades dele. O runner conhece as tarefas antes de rodar. O nome serve de identidade estável para o checkpoint de `registrar-checkpoints-da-geracao`.

2. Contexto da tarefa.

   Dentro de um worker, `self.context` resolve para um `TaskContext`. Em threads, a troca é feita por uma `ContextVar` lida pelo `BaseMigration.context`. No processo filho, o próprio objeto é trocado. O contexto da tarefa tem:

   - conexões de origem próprias, abertas pelas configurações que o `MigrationContext` passa a guardar (`source_configs`, hoje descartadas pela factory);
   - um gravador de segmento no lugar do output;
   - um coletor de métricas privado;
   - log, progresso e avisos que viram registros para o processo principal (decisão 5).

   Mapas de referência, limite de debug, configuração e o adapter de destino são compartilhados para leitura. O adapter de destino só renderiza: as leituras do destino já estão fechadas quando o `load_transformed_data` abre.

   Uma tarefa só emite `Insert`, `Comment` e `Native`. `Clear`, `SuspendConstraints` e `ResetIdentity` dentro dela são erro, porque mudam o estado da execução (`suspended_constraints`) e pertencem às fases de preparação e finalização.

   Os adapters de origem guardam o dono, processo e thread. Usados por outro, levantam erro com a explicação. Isso pega um engine criado fora da tarefa (no `__init__`, por exemplo) e usado dentro dela, o que faria duas threads dividirem uma conexão.

3. Segmentos e junção.

   Com mais de 1 worker, cada tarefa grava os comandos num segmento `tarefas/<NN>_<fase>/<nome>.<ext>`, renderizados pelo adapter de destino e gravados sem tradução de quebra de linha. O processo principal junta os segmentos na ordem da decisão 1 assim que o prefixo fica completo: o segmento da tarefa *i* entra quando as tarefas 1 a *i* terminaram. A junção usa `output.include_file(segmento)`. O segmento é apagado em seguida.

   O arquivo da fase sai igual ao da geração em sequência quando as tarefas estão declaradas na ordem em que o `run_inserts()` as chamava e as consultas têm `order_by`. Sem `order_by`, a ordem das linhas já depende do banco hoje.

   Com 1 worker, as tarefas rodam em sequência direto no output, sem segmentos.

4. Executores.

   `--workers N` e `--worker-mode thread|process`. O padrão é 1 worker em modo thread.

   Threads: um `ThreadPoolExecutor`, uma conexão de origem por thread e um evento de cancelamento conferido no `engine.insert()`. As conexões das threads são fechadas depois do `join`.

   Processos: N processos criados de uma vez, no início do `run_tasks()`, pelo contexto `fork` (`multiprocessing.get_context('fork')`), a partir da thread principal. Sem `Pool` nem `ProcessPoolExecutor`: as threads auxiliares deles fariam os forks seguintes acontecerem num processo com várias threads, o que o Python 3.12 avisa e que pode travar. As tarefas são distribuídas por um contador compartilhado e os resultados voltam por uma fila. O filho:

   - ignora SIGINT, deixando a interrupção com o processo principal;
   - abre conexões de origem novas e nunca fecha as herdadas, porque fechar a conexão herdada do psycopg2 derruba a do processo principal;
   - sai com `os._exit`, sem finalizadores.

   Antes do fork, o processo principal descarrega o stdout e o buffer do output, para o filho não gravar de novo os mesmos bytes.

   O modo processo só existe em Linux, onde roda a imagem Docker. Ele é recusado, antes de qualquer conexão, quando algum adapter é MongoDB: o pymongo mantém threads de monitoramento. Um fork com elas pode travar. Com MongoDB, threads servem: o `MongoClient` é seguro entre threads.

   Threads continuam o padrão porque funcionam com todos os drivers e não têm os riscos do fork. Elas ganham quando o tempo está na espera da origem. Processos ganham quando o tempo está em CPU. O relatório da geração mostra qual é o caso (decisão 8).

5. Um só escritor.

   Workers não usam logger, progresso nem métricas diretamente. Eles mandam registros para o processo principal: linhas de log, eventos, progresso, avisos e as métricas das unidades. O processo principal é o único que escreve no `migration.log`, no `migration.jsonl` e no terminal. O rodapé de progresso sai antes das tarefas e dá lugar a linhas `k de n tarefas concluídas`.

   O `GenerationMetrics` deixa de depender da pilha para contar: o engine guarda a própria `Unit`. O coletor do processo principal adota as unidades das tarefas na ordem de junção, com `Unit.from_dict` para as que vêm de processos. O `warn_once` deduplica no processo principal, porque o conjunto de avisos não é compartilhado entre processos.

6. Cópia das entidades compatíveis.

   Com mais de 1 worker, cada `CopyEntity` vira uma tarefa com nome automático (`copy:<entidade>`), sem decorator. A ordem de junção por origem continua a de hoje: as entidades copiadas linha a linha e depois a cópia nativa. O `pg_dump` vira uma tarefa cujo arquivo temporário é o segmento.

7. Output direto e híbrido.

   A execução no destino continua em série, no processo principal, pelo `include_file()` de cada segmento. A falha de um comando aparece quando o segmento dele é juntado, não quando a tarefa o gerou. Com a política `stop`, essa falha cancela as tarefas que ainda rodam.

8. Falha, interrupção e registro.

   O primeiro erro de uma tarefa cancela as outras: as que não começaram não começam. As que rodam param no próximo `engine.insert()`. A geração falha com o nome da tarefa e a mensagem. Ctrl+C no processo principal faz o mesmo, fecha as conexões dos workers e grava `failed` no manifesto.

   O manifesto registra `workers` e `worker_mode`. O `relatorio-geracao.json` registra, por tarefa, nome, sequência, worker, início, fim e duração. Ele também mostra o tempo total da fase ao lado da soma das tarefas.

9. AcmeEdu e documentação.

   A AcmeEdu troca o `run_inserts()` pelos decorators nos seis `insert_*`, na mesma ordem e todos na sequência 1. O `docs/conversao-clientes.md` e o `docs/novo-cliente.md` mostram a conversão. A documentação traz as regras para o script do cliente: tarefas da mesma sequência não dependem do resultado umas das outras. Estratégias e closures não alteram estado compartilhado. Em processos, o estado alterado numa tarefa não volta ao processo principal.

## Risks / Trade-offs

- Ganho incerto com threads -> o GIL limita a CPU. Mitigação: medir a AcmeEdu com 1, 2 e 4 workers nos dois modos e registrar no design antes de arquivar.
- Mais conexões na origem -> N workers abrem até N conexões por origem. Documentar e limitar pelo `--workers`.
- Fork com conexões herdadas -> o filho nunca fecha nem usa as conexões herdadas. O modo processo é recusado com MongoDB.
- Script do cliente com estado compartilhado -> uma tarefa que escreve num dicionário lido por outra da mesma sequência gera resultado diferente em paralelo. A regra fica documentada. A sequência resolve a dependência.
- Erro no destino aparece mais tarde no output direto -> só quando o segmento é juntado.

## Migration Plan

1. Implementar e arquivar `preservar-quebras-de-linha-nos-artefatos`.
2. Criar o decorator e o `run_tasks()` com execução em sequência, sem mudar os artefatos.
3. Criar o contexto da tarefa, os segmentos e a junção ordenada, com o executor de threads.
4. Criar o canal de registros e mudar métricas, log e progresso para um só escritor.
5. Criar o executor de processos.
6. Levar a cópia compatível para tarefas.
7. Converter a AcmeEdu, medir e documentar.

Rollback: reverter os commits. Sem tarefas declaradas, ou com 1 worker, a geração é a de hoje.

## Open Questions

- Qual modo deve ser o padrão depois das medições? Threads ficam como padrão até uma carga real mostrar onde está o tempo.
- Vale aceitar `after='nome'` além da sequência, para dependências que não formam camadas?
