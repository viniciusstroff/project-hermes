# Arquitetura da Migração Acme

## Visão geral

O script `Migracao.py` é uma aplicação CLI que:

1. Lê a configuração do Acme em `Acme/.env`.
2. Abre as conexões declaradas no `.env` do cliente: uma ou mais origens e um destino.
3. Gera os artefatos de cada fase no formato do destino: `.sql` com `TRUNCATE`, `INSERT`, `ALTER TABLE` e `setval` no PostgreSQL.
4. Por padrão, não aplica os dados na V2 durante a geração.
5. Deixa a aplicação para depois, sobre os arquivos gerados em `runs/<cliente>/<timestamp>/comandos/`. Quem aplica é o `Aplicar.py` (ou o próprio `Migracao.py` com `--apply` ou `--dry-run`), numa única transação. Com `--output direct` ou `hybrid`, os comandos rodam no destino durante a geração, também numa transação.

## Bancos envolvidos

O `core/connections.py` lê as conexões que o `.env` do cliente declara por nome:

- cada origem é um grupo `SOURCE_<NOME>_*` com a chave `SOURCE_<NOME>_ADAPTER`;
- o destino é `TARGET_ADAPTER` com as demais chaves `TARGET_*`;
- o resto do nome de cada chave, em minúsculas, é parâmetro do adapter (`SOURCE_V1_HOST` vira `host` da origem `v1`).

Quando dois nomes de origem começam igual, a chave fica com o nome declarado mais longo: `SOURCE_V1_LEGACY_HOST` é da origem `v1_legacy`, não da `v1`. Uma chave `SOURCE_` sem dono interrompe a execução antes de conectar.

O registro em `core/adapters/registry.py` resolve o nome do adapter para a classe de origem ou de destino. Os embutidos são `postgres` (origem e destino), `firebird` (só origem) e `mongodb` (origem e destino). Qualquer outro valor no formato `pacote.modulo:Classe` é importado e conferido contra o contrato do papel (`connect`, `read`, `count` e `close` na origem; `connect`, `render`, `read` e `close` no destino). Cada adapter importa o driver dentro do próprio `connect`, então um cliente só com PostgreSQL roda sem `firebirdsql`.

Na AcmeEdu e no Acme, a origem principal se chama `v1`. Ela é a fonte das leituras e da cópia das entidades compatíveis. O destino é lido para os mapas de referência e para a lista de tabelas da suspensão de triggers. O Acme declara ainda a origem opcional `fb`, de Firebird, para algumas rotinas específicas.

## Estrutura lógica do script

O script de cada cliente declara a migração, mas o runtime compartilhado é
montado por composição. A classe `BaseMigration` continua disponível para
compatibilidade, porém delega as responsabilidades de infraestrutura para
componentes menores.

Componentes principais:

- `MigrationContext`: agrupa configuração, artefatos, adapters de origem,
  adapter de destino, output, logger e progresso. O `emit(operacao)` dele é o
  único caminho de escrita da execução.
- `DefaultMigrationContextFactory`: cria o contexto padrão a partir das conexões
  nomeadas do `.env`, conectando cada uma pelo registro de adapters. O manifesto
  registra o adapter, os parâmetros e a versão do servidor de cada conexão,
  sem senha nem URI.
- `MigrationRunner`: orquestra as fases padrão da execução.
- `SourceAdapter`: lê a origem por `read(query)` e conta as linhas por
  `count(query)`. A consulta é um `SourceQuery` (entidade, campos, filtros de
  igualdade, ordenação e limite inteiro) ou um `NativeQuery`, presa a um adapter.
  O adapter traduz para a linguagem dele, como `LIMIT` no PostgreSQL e `ROWS`
  no Firebird. Ele recusa consulta ou campo nativo de outro adapter antes de ler.
  Os adapters SQL partem de `SqlSourceAdapter` (`core/adapters/sql_source.py`).
- Operações de escrita (`core/operations.py`): `Insert`, `Clear`,
  `SuspendConstraints`, `ResumeConstraints`, `ResetIdentity`, `Native` e
  `Comment`. A migração descreve o que fazer, sem linguagem de banco.
- `TargetAdapter`: traduz cada operação nos comandos do formato dele
  (`render(operacao)`), lê dados de referência (`read(query)`) e declara o
  formato dos artefatos (`artifact_format`). O PostgreSQL usa o `Dialect` para
  os valores e põe aspas nos nomes que precisam. Uma operação sem sentido num
  banco vira nenhum comando. Um `Native` de outro adapter é recusado.
- Estratégias de campo (`core/strategies.py`): devolvem valores Python pelo `raw_value()` e não geram SQL.
  O adapter de destino é o único ponto de serialização. Código de um banco específico entra só por
  `NativeField` e `NativeQuery`, presos ao adapter de origem e conferidos antes da consulta, ou por
  `SqlLiteral`, copiado sem mudança para o destino. O catálogo está em [estrategias.md](estrategias.md).
- `OutputAdapter`: recebe as operações, fase por fase. A implementação padrão é
  `FileOutput`, que pede ao adapter de destino os comandos de cada operação e
  grava em `runs/<cliente>/<timestamp>/comandos/<NN>_<fase>.<extensão>`.
  O `MirrorOutput` entrega as mesmas operações para mais de um output.
- `TargetSession` e `Capabilities` (`core/application.py`): aplicam os
  artefatos no destino, depois da geração. Ver [Aplicação no destino](#aplicação-no-destino).
- Versões (`core/versions.py`): cada adapter declara a versão mínima do
  servidor (`supported_versions`) e confere ao conectar.
- `BaseMigration`: fachada para scripts que usam herança, sobre o mesmo contexto.

### Contratos por papel

| Papel | Contrato | Embutidos | Onde |
|---|---|---|---|
| origem | `connect`, `read(query)`, `count(query)`, `close` | `postgres`, `firebird`, `mongodb` | `core/adapters/` |
| destino, geração | `connect`, `render(operacao)`, `read(query)`, `close_reads`, `close`, `artifact_format` | `postgres`, `mongodb` | `core/adapters/` |
| destino, aplicação | `capabilities`, `session()` com `TargetSession` | `postgres`, `mongodb` | `core/adapters/` e `core/application.py` |
| saída | `OutputAdapter`: `open_phase`, `write(operacao)`, `include_file`, `flush`, `finish`, `describe`, `close` | `FileOutput`, `MirrorOutput` | `core/outputs/` |

| Adapter | Origem | Destino | Formato | Transação | Savepoint | Driver |
|---|---|---|---|---|---|---|
| `postgres` | 9.6 ou mais novo | 12 ou mais novo | `.sql` | sim | sim | `psycopg2` |
| `firebird` | 2.5 ou mais novo | não | | | | `firebirdsql` |
| `mongodb` | 4.4 ou mais novo | 4.4 ou mais novo | `.jsonl` | só em replica set | não | `pymongo` |

Um adapter de fora do core implementa o contrato do papel e entra no `.env`
pelo caminho `pacote.modulo:Classe`. O registro confere os métodos antes de usar.

Responsabilidades do cliente:

- listar em `compatible_entities()` as tabelas que passam sem transformação;
- declarar SQL bruto específico do cliente quando necessário;
- declarar migrações de tabelas, transformações, inserts e updates;
- limpar tabelas, suspender triggers e ajustar sequences pelos atalhos da
  fachada (`clear()`, `suspend_constraints()`, `reset_identity()`).

SQL PostgreSQL puro continua permitido no cliente como escape hatch, dentro de
um `Native('postgres', sql)`. A regra prática é: lógica excepcional do cliente
pode ficar no Acme; operações recorrentes de infraestrutura devem virar
operação do core, traduzida pelo adapter.

## API de composição e compatibilidade

O caminho preferido para novas integrações é receber um `MigrationContext` por
composição e usar `context.source('v1')`, `context.source('fb')` e
`context.emit(...)`. Pedir uma origem que o `.env` não declara gera um erro com
o nome pedido e a lista das origens declaradas. Para escrever fora de um
engine, emita uma operação com `context.emit(...)`. O `BaseMigration`
usa esse mesmo contexto por baixo, então os scripts antigos podem continuar
herdando da base durante a transição.

O contexto expõe a saída só por `context.output`. O antigo `context.writer` e a
classe `SqlArtifactWriter` foram removidos. Quem monta um `MigrationContext` à
mão passa o output no argumento `output=`.

Na fachada por herança, a escrita é `emit(operacao)` e os atalhos `comment()`,
`clear()`, `suspend_constraints()` e `reset_identity()`. Saíram `write_sql()`,
`print_comment()`, `sql_value()`, `_open_sql_file()`, `_flush_writes()`, o
helper de índices e o `write_reset_sequences()`. A propriedade `file` devolve o
arquivo aberto pelo `FileOutput` ou `None` quando o output não grava em
arquivo.

Cada `SuspendConstraints` emitido fica registrado no contexto. Na fase final, o
runner emite o `ResumeConstraints` das mesmas entidades, então o cliente não
precisa lembrar de reabilitar as triggers.

O helper de índices saiu sem substituto. Com nome de índice ele não fazia nada,
e com nome de tabela deixava o índice sem as linhas carregadas. Desligar índice
durante a carga pode voltar como otimização de um adapter.

A fachada não expõe conexões de driver. Código de cliente deve usar adapters,
operações e helpers como `reference_map()`.

## Mapas de referência

O cliente declara os mapas lidos do destino em `declare_reference_maps()`, com
objetos `ReferenceMap` de `core/references.py`: tabela, coluna da chave, coluna
do valor e filtros de igualdade, ou uma `query` que é uma `NativeQuery` do
adapter de destino. O `MigrationRunner` carrega todos antes de abrir
`prepare_target`. Ele registra no log cada mapa com a quantidade de
chaves. O código do cliente lê um mapa com `self.reference_map(nome)`.

O adapter de destino lê os pares pelo mesmo `read()` das origens: a declaração
com tabela vira um `SourceQuery` com a chave, o valor e os filtros
(`reference_query()`). A `NativeQuery` vai como está. A normalização, o
descarte de chave nula e a recusa de chave repetida com valores diferentes
ficam em `build_reference_map()`, iguais para qualquer adapter. O
mapa carregado é um `Mapping` só de leitura que guarda o normalizador. O
`Lookup` usa esse normalizador quando não recebe outro.

Logo depois da carga, o runner chama `context.close_target_reads()`, que chama
o `close_reads()` do adapter. Antes de fechar, o adapter PostgreSQL guarda a
lista de tabelas que `SuspendConstraints` e `ResumeConstraints` sem entidades
vão usar. A partir daí ele recusa consultar o destino (`TargetReadClosedError`).
A transação de leitura termina em `ROLLBACK`, sem segurar lock nas tabelas. A garantia não cobre consultas
que o cliente faça direto pela conexão do adapter de destino
(`context.target.connection`).

## Saída da migração

O runtime não abre arquivos por conta própria. Ele entrega as operações para
um `OutputAdapter`, definido em `core/outputs/base.py`:

```python
class OutputAdapter(Protocol):
    def open_phase(self, phase: str) -> None: ...
    def write(self, operation) -> None: ...
    def include_file(self, path: str) -> None: ...
    def flush(self) -> None: ...
    def finish(self, success: bool) -> None: ...
    def describe(self) -> dict: ...
    def close(self) -> None: ...
```

As fases são chaves lógicas: `setup`, `prepare_target`, `copy_compatible_entities`,
`load_transformed_data` e `finalize_target`. A `DefaultMigrationContextFactory`
abre `setup` ao montar o contexto, depois de conectar o destino, porque a
extensão do arquivo vem do adapter. O `MigrationRunner` abre as outras quatro na
ordem padrão, copia as entidades de `compatible_entities()`, emite a retomada
das triggers suspensas e chama `flush()`. Os engines criados por
`context.source()` escrevem pelo mesmo `context.emit`.

O output fica sabendo do resultado pelo `finish(success)`, que o runner chama
uma vez por execução. Na execução que termina bem, `finish(True)` vem depois do
último `flush()` e antes de o manifesto receber `success`. Se o próprio
`finish(True)` falhar, a execução fica `failed` e o runner não chama
`finish(False)` em seguida. Quando um hook levanta exceção, o runner chama
`finish(False)` e grava `failed`. Um erro dentro desse `finish(False)` vai para
o log sem trocar o erro registrado no manifesto.

O `close()` só libera recursos e acontece em `MigrationContext.close()`, que no
caminho por herança roda a partir do `__del__` do `BaseMigration`. Por isso o
resultado não passa pelo `close()`. Um output fechado sem `finish(True)` deve
tratar a execução como não confirmada, o que para um output de banco significa
`ROLLBACK`.

O `describe()` devolve um dicionário que o manifesto grava na chave `output`.
Com o contexto padrão e destino PostgreSQL o valor é
`{"type": "file", "format": "sql"}`.

A implementação padrão é `FileOutput`, em `core/outputs/file.py`. Ela recebe o
adapter de destino, pede os comandos de cada operação (`render()`) e grava no
arquivo da fase. O caminho vem do `ArtifactManager.command_path(fase, extensão)`,
que também registra o artefato para a lista do manifesto. Os comandos ficam em
um buffer de até 500 antes de ir para o arquivo. Trocar de fase, chamar
`flush()` ou `close()` grava o que estiver pendente. Um `Comment` vira o mesmo
bloco `-----` de antes no SQL. O `finish()` grava o buffer nos dois resultados:
quando a execução falha, os arquivos parciais ficam no disco. Um valor que o
destino não guarda falha no `write()`, com tabela e coluna na mensagem
(`ValueSerializationError`). O `include_file()` copia para a fase um arquivo já
no formato do destino, como a saída do `pg_dump` da cópia nativa.

O `MirrorOutput`, em `core/outputs/mirror.py`, recebe uma lista de outputs e
repassa cada chamada para todos, na ordem em que foram passados:

```python
output = MirrorOutput(FileOutput(artifacts, context.target), outro_output)
```

`open_phase()`, `write()` e `flush()` param no primeiro erro, que
derruba a execução. `finish()` e `close()` chegam a todos os outputs mesmo que
um falhe, e o primeiro erro é relançado no fim. O `describe()` devolve
`{"type": "mirror", "outputs": [...]}` com a descrição de cada output. Com
`MirrorOutput`, a propriedade `BaseMigration.file` devolve `None`.

O `DirectDatabaseOutput`, em `core/outputs/direct.py`, executa no destino os
comandos de cada operação durante a geração. O `write()` pede os comandos ao
adapter de destino do contexto (`render()`), lê o texto com
`session.commands()` e entrega cada comando à mesma política de erro do
aplicador, antes de retornar. Ele não separa comandos nem decide o que pular:
isso é da sessão do adapter. Uma sessão que junta comandos em lote (MongoDB)
roda o lote quando ele enche, na troca de fase ou no fim.

A sessão de escrita tem conexão própria, aberta pelo `session_opener()` com
`waits_between_commands=True` quando a fase `prepare_target` abre. As
operações de `setup` esperam na memória até lá, porque a factory abre essa
fase antes de o runner carregar os mapas de referência. A troca de fase
esvazia a política e chama `end_artifact()` da sessão. O `include_file()`
lê a saída do `pg_dump` linha a linha pela sessão. O `finish(True)` termina
em `COMMIT` no `apply` e em `ROLLBACK` no `dry-run`. O `finish(False)` e o
`close()` sem `finish(True)` terminam em `ROLLBACK`. As capacidades são
conferidas duas vezes: as declaradas pelo adapter quando o output é montado e
as da sessão quando ela abre.

O output é escolhido pelo `--output` do `Migracao.py`: `file` (padrão),
`direct` ou `hybrid`, que é `MirrorOutput(FileOutput, DirectDatabaseOutput)`,
com o arquivo primeiro. Para a escolha chegar à factory sem mudar o
construtor dos clientes, o `Migracao.py` instancia o cliente dentro de
`core.context.output_settings(OutputSettings(...))`, que guarda a escolha numa
`ContextVar`. Fora desse bloco, a factory monta o `FileOutput`.

## Aplicação no destino

O aplicador não conhece linguagem de banco. O contrato fica em
`core/application.py`: `Capabilities` (transação, savepoint, suspender
restrições), `Command` (texto, linha, motivo para pular ou erro) e
`TargetSession` (`begin`, `commands`, `execute`, `savepoint`, `rollback_to`,
`release`, `end_artifact`, `commit`, `rollback`, `error_code`, `describe`).
Cada adapter de destino declara `capabilities` e abre a sua sessão com
`session(waits_between_commands=False)`. O `commands(lines, dry_run,
first_line, source)` lê qualquer sequência de linhas: o aplicador passa o
arquivo aberto e o output direto passa o texto de cada operação, com a linha
em que ele começaria no arquivo da fase. Com `waits_between_commands=True`, a
sessão do PostgreSQL desliga o `idle_in_transaction_session_timeout` no
`begin()` e de novo depois de cada `RESET ALL`.

O código neutro fica em `core/apply/`:

| Módulo | Papel |
|---|---|
| `run.py` | lê o `manifest.json`: adapter de destino e lista de artefatos com fase, caminho e formato, recusando execuções incompletas |
| `execution.py` | modos (`apply`, `dry-run`), políticas de erro (`stop`, `continue`) e o tratamento de cada comando, comuns ao aplicador e ao output direto |
| `applier.py` | `ArtifactApplier`: confere capacidades e formato, abre a sessão e aplica os comandos em transação única, com modo e política de erro |
| `report.py` | grava `relatorio.json` e `erros.txt` em `aplicacoes/<timestamp>-<modo>/` |
| `cli.py` | fluxo de console comum ao `Aplicar.py` e ao `Migracao.py --apply`, com a conexão de `TARGET_*` |

O que é do PostgreSQL fica no adapter: `core/adapters/postgres_splitter.py`
separa os `.sql` em comandos e `core/adapters/postgres_session.py` tem as
regras de comandos pulados, o `RESET ALL`, o `standard_conforming_strings`
lido do servidor, os savepoints e o `SQLSTATE` como código de erro.

O adapter MongoDB (`core/adapters/mongodb.py`) tem a própria sessão. Ela lê os
`.jsonl` linha a linha (`Command.payload` guarda a operação já lida), declara
transação só em replica set e nenhum savepoint. Os inserts seguidos na mesma
coleção ficam pendentes e vão num `insert_many` de até 1000. Por isso a sessão
expõe `pending` e `flush()`. Um erro de lote chega como `DeferredCommandError`,
com a posição do comando entre os pendentes. A `StopPolicy` usa essa
posição para registrar no relatório o comando certo do artefato.

### Por que depois da geração

Quando o aplicador foi criado, a geração lia o destino enquanto gerava os
inserts: o antigo `load_reference_map()` consultava a V2 no meio do
`run_inserts()`. Se os comandos fossem executados durante a geração, essa
leitura veria o destino no meio da carga. Na mesma conexão, uma tabela truncada
pelo `01_prepare_target` apareceria vazia. Numa conexão separada, a leitura
esperaria o lock do `TRUNCATE`, que só sai no fim da transação.

Esse impedimento acabou: os mapas de referência são carregados antes da
primeira fase e as leituras do destino ficam fechadas depois disso. A saída do
`pg_dump` entra pelo `include_file()` do output. Por isso o
`DirectDatabaseOutput` existe hoje como alternativa (ver
[Saída da migração](#saída-da-migração)). O aplicador continua o padrão.

Aplicando a pasta da execução depois, a aplicação pode rodar em outra máquina.
O `GeneratedRun` resolve os artefatos do manifesto pelos caminhos relativos à
pasta da execução. Um caminho que saia dela é recusado.

### Separador de comandos do PostgreSQL

O `split_sql_file()` do adapter PostgreSQL lê o arquivo linha a linha e devolve objetos `Statement`
com o tipo (`sql` ou `meta-command`), o texto sem `;` final e a linha inicial.
Um `;` só encerra o comando fora de strings `'...'` e `E'...'`, identificadores
`"..."`, dollar quotes `$tag$...$tag$` e comentários, inclusive `/* */`
aninhados. O separador acompanha os `SET standard_conforming_strings` do
arquivo, porque com essa configuração desligada a barra invertida também escapa
a aspa em `'...'`. O valor inicial vem do servidor.

Internamente é uma máquina de estados (padrão State). O `_StatementSplitter`
guarda o estado atual. Cada estado é uma classe com um método `scan()`:
`_Gap` entre comandos, `_InStatement` dentro de um comando e uma subclasse de
`_Literal` para cada tipo de literal. Para incluir um literal novo, basta criar
a classe e registrá-la em `_LITERALS`.

A separação foi comparada com a do `psql` 16 e coincidiu em todas as entradas
testadas. A diferença conhecida é a barra invertida no meio de um comando: o
`psql` executa um metacomando ali e o separador manda o texto ao servidor.

### Aplicador

O `ArtifactApplier(run, open_session, mode, on_error, batch_size=1000)` lê a
execução, resolve o adapter de destino pelo registro e confere o formato dos
artefatos e as capacidades declaradas. A política padrão vem daí: `stop` no
`apply` e, no dry run, `continue` quando o destino tem savepoint. A função
`open_session` só é chamada depois disso, então uma execução incompleta ou um
modo que o destino não cumpre não abre conexão. Com a sessão aberta, ele confere
de novo as capacidades, que podem ser menores que as declaradas.

Duas escolhas definem uma aplicação, cada uma representada por um objeto:

- o modo (`_ApplyMode` ou `_DryRunMode`) decide como a transação termina e a
  política padrão;
- a política de erro (`_StopPolicy` ou `_ContinuePolicy`) decide o que fazer
  quando um comando falha.

A sessão marca os comandos que não executa (`Command.skip`) e os que encerram
a aplicação (`Command.error`). No PostgreSQL são pulados os metacomandos do
`psql`, o controle de transação e, no dry run, `setval`. Um `COPY ... FROM
stdin` encerra a aplicação. Depois de cada arquivo a sessão executa
`RESET ALL`, porque antes cada arquivo rodava numa sessão de `psql` e as
configurações do dump morriam com ela.

A `_ContinuePolicy` abre um savepoint por lote. Se um comando falha, volta ao
savepoint, reexecuta o lote comando a comando com um savepoint para cada um e
abre um lote novo no comando seguinte. Isso não mantém a transação abaixo de 64
subtransações: uma subtransação que gravou dados fica na lista até o fim, mesmo
depois do `RELEASE SAVEPOINT`. Uma carga de 1 milhão de comandos em lotes de 1000
cria 1000 subtransações. Com o destino isolado isso é aceitável, mas precisa ser
medido numa carga real antes de usar `continue` num banco com réplica.

Falhas esperadas (comando com erro no `stop`, comando não suportado, aspas sem
fechamento, `COMMIT` recusado) terminam em `ROLLBACK` e devolvem o relatório com
resultado `failed`. Qualquer outra exceção, inclusive Ctrl+C, também faz
`ROLLBACK` e grava o relatório antes de ser relançada.

O uso pela linha de comando, o formato do relatório e os cuidados de operação
estão em [execucao.md](execucao.md#aplicando-os-sqls-no-destino).

## Tarefas e paralelismo

O script do cliente declara as cargas como tarefas, com o decorator de
`core/tasks.py`:

```python
class Migracao(BaseMigration):
    @task('courses')
    def insert_courses(self): ...

    @task('enrollments', sequence=2)
    def insert_enrollments(self): ...
```

O nome é único na classe. Uma classe com nome repetido é recusada na
definição. Tarefas da mesma sequência são independentes. Uma sequência maior
espera as menores. A ordem de junção segue a sequência. Dentro dela, segue a ordem
de declaração. O `run_inserts()` padrão do `BaseMigration` chama `run_tasks()`,
que monta a lista e entrega ao `run_task_list()` de `core/parallel.py`.

Com 1 worker, cada tarefa roda no contexto da execução e escreve direto no
output, como antes. Com mais, cada tarefa roda num `TaskContext`: conexões de
origem próprias (o contexto guarda as `source_configs`), um `FileOutput` que
grava num segmento em `tarefas/<NN>_<fase>/` e um `GenerationMetrics` privado.
Dentro do worker, `self.context` resolve para esse contexto por uma
`ContextVar`. O script não muda. Em qualquer número de workers, uma tarefa
só emite `Insert`, `Comment` e `Native`.

O `_Coordinator` roda no processo principal. Ele recebe os registros dos
workers (linhas de log, eventos, avisos, cópias nativas e o resultado de cada
tarefa) e é o único que escreve no log, no terminal e nas métricas. Quando as
tarefas anteriores a uma já terminaram, ele junta o segmento dela à fase com
`output.include_file()`, adota as unidades dela no coletor e apaga o segmento.

Os executores:

- `_ThreadRunner`: um `ThreadPoolExecutor`, uma conexão de origem por thread e
  um evento de cancelamento que o `engine.insert()` confere;
- `_ProcessRunner`: N processos criados de uma vez pelo contexto `fork`, sem
  `Pool`, com as tarefas distribuídas por um contador compartilhado e os
  registros por uma fila. O filho ignora SIGINT, abre conexões novas e nunca
  fecha as herdadas, que são do processo principal. Antes do fork, o stdout e
  o buffer do output são descarregados.

Os adapters de origem guardam o processo e a thread que os criou
(`core/adapters/ownership.py`) e recusam leituras de outro worker. Isso pega
um engine criado fora da tarefa e usado dentro dela. O modo processo é
recusado fora do Linux e com MongoDB antes de qualquer conexão, pela
`ParallelSettings.check()`. A cópia de `compatible_entities()` também vira
tarefas com mais de um worker, uma por entidade e uma por cópia nativa, cujo
arquivo do `pg_dump` é o próprio segmento.

## Checkpoint da geração

Com o output `file`, a factory abre um `Checkpoint` (`core/checkpoint.py`) com
`flock` na pasta da execução e monta um `CheckpointedFileOutput`. Esse output
acompanha a posição em bytes e o sha256 de cada fase. Os pontos de controle
são as tarefas: antes de uma tarefa, `begin_task()` guarda a posição; depois
dela, `end_task()` descarrega o arquivo, faz `fsync` e registra no
`checkpoint.json` o nome, as posições, o sha256 do arquivo até o fim da tarefa,
as unidades com as contagens da origem e um hash do código do método. O
`checkpoint.json` é gravado num arquivo temporário e trocado com `os.replace`.
Com mais de um worker, uma tarefa que termina fora de ordem tem o segmento
registrado antes de entrar na fase.

Na retomada, o `Checkpoint` monta um plano por fase. Uma fase fechada é
pulada inteira. A fase em que a falha aconteceu é cortada no fim da última
tarefa confirmada. As fases seguintes começam do zero. Com `--rerun`, o corte fica
no início da tarefa pedida. Depois de os mapas de referência carregarem, o
runner chama `context.begin_checkpoint()`, que confere a impressão digital e
conta de novo, na origem, as unidades das tarefas que não vão rodar.

O `CheckpointedFileOutput` abre uma fase cortada em modo de pulo. Ele confere
o sha256 do trecho confirmado. Depois disso, compara com o arquivo cada
operação renderizada e pula as tarefas confirmadas (`resumed_task()` e
`skip_task()`). Quando a posição chega ao corte, ele trunca o arquivo ali e
passa a gravar. Até esse ponto, o arquivo e o checkpoint da fase não mudam,
então uma retomada recusada deixa a execução como estava.

## Métricas da geração

O contexto tem um coletor, `context.metrics`, definido em `core/metrics.py`. O
`context.source(nome)` passa o coletor e o nome da origem ao engine. Cada
migração roda dentro de uma unidade:

```python
with engine.unit(self.target) as unit:
    values = engine.field_values(self.target, self.fields)
    for row in engine.read(query):
        unit.row_read()
        engine.insert(self.target, values(row))
```

O `engine.insert()` conta o insert na unidade aberta, por entidade. Um insert
fora de uma unidade não é contado. O `engine.field_values()` monta, uma vez por
execução, a função que transforma uma linha nos pares coluna e valor. Nas
estratégias que sobrescrevem `resolve()`, ela registra a perda que a estratégia
informa com um `LostValue` e grava o valor dele. As outras são lidas com
`raw_value()`. Uma exceção que sai do bloco fecha a unidade como `interrupted`.

O contexto abre as fases com `context.open_phase()`, que avisa o coletor e o
output. No fim, o runner chama `context.write_generation_report()`: o coletor
fecha o que estiver aberto, o resumo vai para o log e o
`relatorio-geracao.json` é gravado antes do manifesto, no sucesso e na falha.
Uma falha ao gravar o relatório vai para o log e não muda o resultado da
execução. O manifesto não recebe métricas: ele é reescrito a cada mudança de
status e lido pelo aplicador.

O `MigrationLogger` grava cada linha também em `logs/migration.jsonl`, com a
fase e a unidade correntes, que ele pergunta ao coletor. O `print_log()` aceita
`level` (`info`, `warning` ou `error`). O texto do `migration.log` não muda.

## Artefatos gerados

Os artefatos ficam em `runs/<cliente>/<YYYYmmdd-HHMMSS>/`:

- `manifest.json`
- `logs/migration.log` e `logs/migration.jsonl`
- `relatorio-geracao.json`
- `tarefas/`, só durante uma geração com mais de um worker, com os segmentos que ainda não entraram na fase
- `checkpoint.json` e `checkpoint.lock`, com o output `file`
- `comandos/00_setup.sql`
- `comandos/01_prepare_target.sql`
- `comandos/02_copy_compatible_entities.sql`
- `comandos/03_load_transformed_data.sql`
- `comandos/04_finalize_target.sql`
- com destino MongoDB, os mesmos arquivos com extensão `.jsonl`
- `aplicacoes/<YYYYmmdd-HHMMSS>-<apply|dry-run>/relatorio.json` e `erros.txt`, um par por aplicação

Responsabilidade de cada artefato:

- `00_setup.sql`: arquivo inicial da execução.
- `01_prepare_target.sql`: preparação da V2, com limpeza de dados e desabilitação de triggers.
- `02_copy_compatible_entities.sql`: cópia das entidades de `compatible_entities()`, linha a linha ou com a saída do `pg_dump` no fim.
- `03_load_transformed_data.sql`: carga principal e ajustes intermediários.
- `04_finalize_target.sql`: `setval` de sequences e reativação de triggers.

A extensão vem do adapter de destino. O manifesto lista os artefatos da
execução na chave `artifacts`, com fase, caminho relativo e formato.

## Estratégia de carga

O processo mistura dois modelos:

- cópia das entidades compatíveis, linha a linha pelos adapters ou pelo `pg_dump` quando a cópia nativa está ligada, para tabelas que passam como espelho;
- `INSERT`s gerados em Python para tabelas que exigem mapeamento, filtros, replace, defaults ou correções.

Isso existe porque parte da base V1 não é compatível diretamente com a estrutura da V2.

## Tratamento de incompatibilidades

O script já incorpora decisões de compatibilidade:

- lista de inclusão das entidades compatíveis: uma tabela nova na V1 só entra se for listada;
- limpeza prévia de tabelas da V2 para evitar `duplicate key`;
- correções posteriores, como prefixo de `document_files.f_file` e ajustes em datas;
- atualização de usernames com encoding inválido em IDs específicos.

## Dependências operacionais

Para funcionar, o ambiente precisa ter:

- `python3`
- `python-dotenv`
- o driver de cada adapter usado, de `requirements/<adapter>.txt`: `psycopg2` no `postgres`, `firebirdsql` no `firebird` e `pymongo` no `mongodb`
- `pg_dump`, só com a cópia nativa ligada, da versão do servidor de origem ou mais nova
- servidores dentro da faixa de cada adapter, conferida ao conectar (`supported_versions` e `core/versions.py`)
- acesso de rede aos bancos configurados

O `Aplicar.py` sozinho precisa apenas de Python 3, do driver do adapter de destino e da pasta `core/`.

O diretório `runs/` é criado automaticamente na raiz do projeto e ignorado pelo Git.

## Riscos arquiteturais atuais

- O `main` chama `enable_debug(100)`, o que limita as consultas e impede migração completa se isso não for removido.
- A aplicação em transação única bloqueia o destino até o fim. Um erro perto do fim descarta a carga inteira.
- Os `setval` executados antes de um erro continuam valendo depois do `ROLLBACK`, porque sequences não são transacionais.
- Num destino MongoDB, a transação tem limite de tempo (`transactionLifetimeLimitSeconds`, 60 segundos por padrão). Uma carga grande precisa do limite aumentado antes da aplicação.
- A cópia linha a linha das entidades compatíveis insere todas as colunas. Uma coluna `GENERATED ALWAYS` falha; para essas tabelas, o caminho é o `pg_dump`.
- As listas de entidades compatíveis e de tabelas limpas são estáticas e dependem do histórico de cada cliente.
