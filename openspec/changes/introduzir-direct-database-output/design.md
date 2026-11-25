## Context

O `MigrationRunner` entrega os comandos ao `OutputAdapter` fase a fase. A `DefaultMigrationContextFactory` abre a fase `setup` ao montar o contexto. O runner gera os comandos de trigger, carrega os mapas de referência e fecha as leituras do destino (change `padronizar-lookups-de-referencia`), chama `set_indexes_commands` e abre `prepare_target`, `load_transformed_data` e `finalize_target`, nessa ordem. No fim chama `flush()` e `finish(success)`.

O `Migracao.py` chama `dump_tables()` antes de `migration.run()`, fora do modo debug. O `pg_dump` grava o `02_dump_compatible_tables.sql` direto no disco, sem passar pelo output. O `SqlFileOutput` abre o arquivo da fase com `'w+'`, então abrir essa fase por ele apagaria o dump.

O `SqlArtifactApplier`, em `core/apply/`, aplica os arquivos de uma execução em transação única. Ele separa os comandos com `split_sql_file()`, pula controle de transação e metacomandos do `psql`, pula `setval` no `dry-run`, recusa `COPY ... FROM stdin`, executa `RESET ALL` depois de cada arquivo e grava o relatório em `aplicacoes/<timestamp>-<modo>/`. Modos e políticas de erro são classes internas do `applier.py`.

O contrato já tem o que o output direto precisa para fechar a transação. O `finish(success)` é chamado uma vez por execução. Um `close()` sem `finish(True)` vale como execução não confirmada. O `describe()` vai para a chave `output` do manifesto. O `MirrorOutput` entrega os mesmos comandos a vários outputs, para nas chamadas de entrega no primeiro erro e leva `finish` e `close` a todos.

Os clientes montam o contexto dentro do `BaseMigration.__init__`, chamando `super().__init__(_clientdir)` sem outros parâmetros. O `Migracao.py` só instancia `module.Migracao()`.

## Goals / Non-Goals

**Goals:**

- Executar no destino, durante a geração, os mesmos comandos que o `SqlArtifactApplier` executaria sobre os arquivos, na mesma ordem e com as mesmas regras.
- Falhar no primeiro comando com erro, com a política `stop`, sem esperar a geração terminar.
- Nenhum comando da execução chega ao destino antes de as leituras do destino serem fechadas.
- Escolher o output pela linha de comando, sem mudar o construtor dos clientes.

**Non-Goals:**

- Não criar o `ExecutionAdapter` do item 11. Se ele avançar, este output vira a estratégia `direct-db`.
- Não usar `COPY`, lotes num mesmo `execute` ou staging. O custo por comando fica igual ao do aplicador.
- Não paralelizar geração e execução. As duas rodam na mesma thread (item 12).
- Não criar checkpoints nem retomar uma execução interrompida (item 13).
- Não passar operações estruturadas ao output (item 6). O output continua recebendo texto SQL.
- Não remover o `rodar2.sh` nem mudar o `Aplicar.py`.

## Decisions

1. Executar cada comando assim que ele fica completo.

   O `write()` recebe pedaços de texto que não coincidem com comandos. O `comment()` gera várias linhas de comentário e os comandos de trigger chegam juntos numa string só. O output passa o texto para uma versão incremental do separador de `core/apply/splitter.py`, que recebe pedaços de qualquer tamanho e devolve os comandos completos. Cada comando completo é executado antes de o `write()` retornar. Ao abrir a fase seguinte, o texto pendente vira o último comando da fase, como no fim de um arquivo.

   As linhas são contadas a partir do início de cada fase. É a mesma numeração do arquivo que o `SqlFileOutput` gravaria com o mesmo texto, então no modo `hybrid` a linha do relatório aponta para a linha do arquivo.

   Alternativa considerada: executar cada pedaço do `write()` como veio. Rejeitada porque um pedaço pode ter vários comandos ou só parte de um, o relatório perderia a posição do erro e as regras de comandos pulados precisam olhar comando por comando.

   Alternativa considerada: acumular a fase inteira e executar no fim dela. Rejeitada porque perde a falha antecipada e guarda a fase de carga inteira na memória.

   Custo: uma ida ao banco por comando, como no aplicador e no `psql`. Como geração e execução dividem a mesma thread, o tempo total tende a ficar perto de gerar e aplicar em sequência. O ganho é a falha antecipada e não precisar do `03` em disco no modo `direct-db`.

2. Transação única, com modos e políticas iguais aos do aplicador.

   Os motivos são os da decisão 2 de `aplicar-artefatos-sql-no-destino`: ou a carga entra inteira, ou nada entra. O `finish(True)` termina em `COMMIT` no modo `apply` e em `ROLLBACK` no `dry-run`. O `finish(False)`, ou um `close()` sem `finish(True)`, termina em `ROLLBACK`. Um `COMMIT` que falha faz o `finish(True)` levantar exceção. O runner já grava a execução como `failed` nesse caso.

   As políticas são as do aplicador, com os mesmos padrões: `stop` no `apply` e `continue` no `dry-run`. Com `stop`, o comando que falha faz o `write()` levantar exceção, o runner chama `finish(False)` e o manifesto registra a posição do erro. Com `continue`, o comando que falha é desfeito por savepoint, vai para o relatório e a geração segue. O limite de 64 subtransações descrito na decisão 6 do aplicador vale aqui também.

   O que hoje fica dentro do `applier.py` (modos, regras de comandos pulados, detecção de `COPY ... FROM stdin` e políticas) passa para um módulo compartilhado, `core/apply/execution.py`, usado pelos dois. Os testes atuais do aplicador continuam passando sem mudança.

3. Segurar a fase `setup` até as leituras do destino fecharem.

   A factory abre `setup` ao montar o contexto, antes de o runner carregar os mapas. Se os comandos dessa fase rodassem na hora, poderiam pegar locks em tabelas que o provider lê em seguida por outra conexão. O processo ficaria esperando por ele mesmo. É o mesmo problema que adiou este output. Por isso o `DirectDatabaseOutput` guarda os comandos de `setup` na memória e só os executa quando `prepare_target` abre. O runner só abre `prepare_target` depois do `close_reads()`. Assim nenhum comando da execução chega ao destino antes de as leituras fecharem.

   O core não escreve nada em `setup` hoje. Um cliente que escreva muito nessa fase gasta memória proporcional.

4. Conexão de escrita própria, aberta só depois do `close_reads()`.

   O output abre a própria conexão com as chaves `TARGET_*`, `client_encoding` UTF8 e `autocommit` desligado. Ele não reaproveita a conexão do provider porque o código do cliente ainda pode ler o destino pela conexão do adapter (`context.target.connection`). Na mesma conexão, essa leitura veria a carga em andamento (tabelas truncadas) e montaria dados errados sem aviso. Numa conexão separada, ela espera o lock do `TRUNCATE`: o processo para, mas o log mostra onde. A limitação fica documentada e some quando a `desacoplar-migracao-de-banco-e-versao` tirar a conexão do driver da API do cliente.

   O `psycopg2` abre uma transação no primeiro `SELECT` e só a fecha com `commit` ou `rollback`. Depois de carregar mapas e triggers, a conexão do provider fica parada dentro dessa transação, com locks `ACCESS SHARE` nas tabelas lidas e no catálogo. Um `TRUNCATE` dessas tabelas na conexão de escrita esperaria para sempre. Por isso a change `padronizar-lookups-de-referencia` faz o `close_reads()` encerrar a transação de leitura com `ROLLBACK`. Esta change depende disso.

   A conexão de escrita é aberta com `options='-c idle_in_transaction_session_timeout=0'`. Entre um comando e outro, a sessão espera o Python ler a V1 e transformar as linhas. Num servidor com esse timeout configurado, a sessão cairia e a carga inteira seria perdida. Opções de inicialização sobrevivem ao `RESET ALL` entre fases, porque o `RESET` volta ao valor do início da sessão.

5. O dump entra pelo contrato do output.

   O contrato ganha `include_file(phase, path)`: o conteúdo da fase já está naquele arquivo, gravado fora do output. O `BaseMigration.dump_tables()` registra no contexto o caminho que o `pg_dump` gravou. O runner chama `output.include_file('dump_compatible_tables', path)` depois do `run_initial()` e antes de abrir `load_transformed_data`, só quando a execução tem dump. Assim o runner não precisa deduzir a existência do dump pelo modo debug ou pela presença do arquivo.

   - `SqlFileOutput`: não faz nada. O arquivo já é o artefato da fase.
   - `MirrorOutput`: repassa para cada output, como as outras chamadas de entrega.
   - `DirectDatabaseOutput`: lê o arquivo em streaming com `split_sql_file()`, aplica as mesmas regras (pula o `BEGIN`/`COMMIT` em torno de large objects, o `\restrict` e, no `dry-run`, os `setval`) e executa `RESET ALL` no fim, porque o dump muda o `search_path`.

   Com isso a ordem executada pelo output direto é `00`, `01`, `02`, `03`, `04`, a mesma do `GeneratedRun`.

   Alternativa considerada: levar o `pg_dump` para dentro do runner. Fica de fora. O `pg_dump` lê só a origem, então mudar o momento dele não muda o resultado. A mudança mexeria no fluxo do modo debug e do `Migracao.py` sem ganho para esta change.

6. Mesmo efeito do aplicador.

   Para os mesmos comandos gerados, o `DirectDatabaseOutput` num modo executa os mesmos comandos, na mesma ordem e com os mesmos comandos pulados que o `SqlArtifactApplier` executaria sobre os arquivos no mesmo modo. Os testes conferem isso com uma conexão falsa, comparando a lista de comandos executados pelos dois caminhos.

   A diferença que sobra é o tempo de lock. No output direto, o `TRUNCATE` da fase `prepare_target` segura as tabelas durante toda a geração.

7. Relatório e manifesto.

   O output usa o mesmo `ApplicationReport` em `aplicacoes/<YYYYmmdd-HHMMSS>-<modo>/`, com o nome do arquivo de cada fase (`01_prepare_target.sql` e assim por diante), para que o relatório leia igual ao do aplicador. O `relatorio.json` ganha a chave `source`, com `generation` no output direto e `artifacts` no aplicador.

   O `describe()` devolve, por exemplo, `{"type": "direct-db", "mode": "dry-run", "on_error": "continue", "report": "aplicacoes/20261125-101500-dry-run"}`. No `hybrid`, o manifesto mostra o `MirrorOutput` com as duas descrições. Uma execução validada não se confunde com uma aplicada.

   Com `continue`, uma execução que teve comandos com erro e terminou em `COMMIT` fica `success` no manifesto. O relatório lista os erros. O `Migracao.py` mostra o mesmo resumo que o `run_application()` mostra hoje: resultado, quantidade de erros e pasta do relatório.

8. Escolha do output pelo CLI.

   `--output sql-file|direct-db|hybrid`, com os nomes do roadmap. `hybrid` é `MirrorOutput(SqlFileOutput, DirectDatabaseOutput)`, com o arquivo primeiro. Assim o arquivo recebe o comando que falhou no banco.

   O roadmap previa um nome novo para o dry run do output direto, para não confundir com `--apply` e `--dry-run`. A decisão é manter um significado só para as duas opções: como a transação termina no destino. Sem `--output`, ou com `sql-file`, elas aplicam os arquivos depois da geração, como hoje. Com `direct-db` ou `hybrid`, definem o modo do output direto e nada é aplicado depois. `--output direct-db` sem `--apply` nem `--dry-run` é erro: não existe padrão que escreva no destino. O `--on-error` segue a mesma regra.

   Alternativa considerada: `--output direct-db-dry-run` ou uma opção `--validate`. Rejeitada porque criaria dois nomes para o mesmo fim de transação.

   A escolha não vai para o `.env`. Uma chave ali faria toda execução daquele cliente escrever no destino sem ninguém pedir na linha de comando.

   Para a escolha chegar à factory sem mudar o construtor dos clientes, o `Migracao.py` instancia o cliente dentro de `core.context.output_settings(settings)`, um gerenciador de contexto que guarda a escolha numa `ContextVar` lida pela factory. Fora dele, a factory usa `sql-file`.

   Alternativa considerada: variável de ambiente. Rejeitada pelo mesmo motivo do `.env`: ela continuaria valendo nas próximas execuções do mesmo shell. Alternativa considerada: o construtor de cada cliente receber o output. Rejeitada porque obriga a mudar todos os clientes, inclusive os privados.

## Risks / Trade-offs

- Destino bloqueado durante toda a geração -> aceito, porque a carga já exige janela sem uso concorrente. A janela passa a incluir o tempo de geração.
- Conexão ociosa derrubada no meio de uma carga longa (túnel SSH, proxy, firewall) -> a transação é desfeita e a carga se perde no fim. O timeout de sessão ociosa fica desligado na conexão de escrita, mas os outros limites dependem da rede. Ver Open Questions.
- Cliente que lê o destino pela conexão do adapter no meio da geração fica parado esperando o lock -> documentar. A `desacoplar-migracao-de-banco-e-versao` tira essa conexão da API do cliente.
- Estouro do cache de subtransações com `continue` -> o mesmo do aplicador. Medir numa carga real.
- O separador incremental divergir do separador de arquivo -> os dois usam a mesma máquina de estados. Os testes partem o mesmo texto em pedaços arbitrários e comparam com a separação do arquivo inteiro.
- Erro perto do fim descarta a carga inteira -> é o preço da transação única, igual ao do aplicador.

## Migration Plan

1. Extrair modos, regras e políticas do `applier.py` para `core/apply/execution.py`, com os testes do aplicador sem mudança.
2. Criar a API incremental do separador, com testes de equivalência.
3. Adicionar `include_file()` ao contrato, ao `SqlFileOutput` e ao `MirrorOutput`. Registrar o dump no contexto e entregá-lo pelo runner.
4. Criar o `DirectDatabaseOutput` com testes usando conexão falsa.
5. Montar o output na factory, criar `output_settings()` e a opção `--output` no `Migracao.py`.
6. Validar contra o `target-db` do Docker, com o `AcmeEdu` se já existir: `hybrid` com `--dry-run`, `direct-db` com `--apply` e comparação com o resultado do `Aplicar.py` sobre os mesmos arquivos.
7. Atualizar a documentação.

Rollback: reverter os commits. O padrão `sql-file` e o `Aplicar.py` não mudam de comportamento.

## Open Questions

- Vale manter `direct-db` sem arquivos ou só oferecer `hybrid`? Sem arquivos não dá para auditar nem reaplicar o SQL depois. O ganho é o espaço em disco do `03`.
- O túnel SSH usado para chegar ao destino (`docs/execucao.md`) derruba conexão ociosa? Se derrubar, o `direct-db` só deve ser usado de dentro da rede do destino. Precisa medir com uma geração longa antes de recomendar.
- O ganho compensa? Sem paralelismo, o tempo total fica perto de gerar e aplicar em sequência. A falha antecipada e o disco são os ganhos certos. O tempo precisa ser medido numa carga real.
- `--debug` com `--output direct-db --apply` grava uma carga parcial no destino. Essa combinação deve ser recusada? Hoje `--debug --apply` sobre arquivos já é permitido.
