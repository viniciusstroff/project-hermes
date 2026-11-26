## Context

Depois da `desacoplar-migracao-de-banco-e-versao`, a geração e a aplicação funcionam assim:

- O `MigrationRunner` entrega operações (`Insert`, `Clear`, `SuspendConstraints`, `Native`, `Comment`...) ao `OutputAdapter`, fase a fase: `setup`, `prepare_target`, `copy_compatible_entities`, `load_transformed_data` e `finalize_target`. Antes de abrir `prepare_target`, ele carrega os mapas de referência e fecha as leituras do destino (`context.close_target_reads()`).
- O `FileOutput` pede ao adapter de destino os comandos de cada operação (`render()`) e grava em `comandos/<NN>_<fase>.<extensão>`. Os comandos já vêm completos, terminados em quebra de linha. Um `Comment` vira um bloco de comentário no SQL e nada no `.jsonl`.
- A cópia nativa (`pg_dump`) grava um arquivo à parte, que entra na fase `copy_compatible_entities` pelo `include_file(path)` do output.
- O `ArtifactApplier` lê o manifesto, abre uma `TargetSession` do adapter e aplica os comandos em transação única. A sessão lê cada artefato em `Command`, marca os que não executa (`skip`) e os que encerram a aplicação (`error`), executa, abre savepoints, termina a transação e dá o código de um erro. As capacidades do adapter (transação e savepoint) escolhem a política padrão e recusam o que o destino não cumpre. Uma sessão pode adiar comandos para rodá-los em lote (`pending`, `flush()` e `DeferredCommandError`), como o MongoDB faz com os inserts.
- Modos e políticas são classes internas do `applier.py`.
- O contrato de output já tem o que um output de banco precisa para fechar a transação: o `finish(success)` é chamado uma vez por execução. Um `close()` sem `finish(True)` vale como execução não confirmada.
- Os clientes montam o contexto dentro do `BaseMigration.__init__`, chamando `super().__init__(_clientdir)` sem outros parâmetros. O `Migracao.py` só instancia `module.Migracao()`.

## Goals / Non-Goals

**Goals:**

- Executar no destino, durante a geração, os mesmos comandos que o `ArtifactApplier` executaria sobre os arquivos, na mesma ordem e com as mesmas regras, para qualquer adapter de destino com sessão.
- Falhar no primeiro comando com erro, com a política `stop`, sem esperar a geração terminar.
- Nenhum comando da execução chega ao destino antes de as leituras do destino fecharem.
- Escolher o output pela linha de comando, sem mudar o construtor dos clientes.

**Non-Goals:**

- Não criar o `ExecutionAdapter` do item 11. Se ele avançar, este output vira a estratégia `direct`.
- Não usar `COPY`, staging ou lotes além dos que a sessão do adapter já faz.
- Não paralelizar geração e execução. As duas rodam na mesma thread (item 12).
- Não criar checkpoints nem retomar uma execução interrompida (item 13).
- Não remover o `rodar2.sh` nem mudar o `Aplicar.py`.

## Decisions

1. Entregar os comandos de cada operação à sessão antes de o `write()` retornar.

   O `write(operation)` pede ao adapter de destino os comandos da operação (`render()`), passa o texto pela sessão (`commands()`) e entrega cada `Command` à política de erro, que chama o `execute()` da sessão. Cada operação renderiza comandos completos, então não há texto pendente entre um `write()` e outro e não é preciso separador incremental. Um `Native` com vários comandos vira vários `Command`. No PostgreSQL, um `Comment` vira um comentário que o separador descarta. No MongoDB, não vira nenhuma linha.

   A sessão do PostgreSQL roda cada comando no `execute()`, então o comando já rodou quando o `write()` retorna. A do MongoDB junta os inserts em lote e roda o lote quando ele enche, quando chega um comando de outro tipo, na troca de fase ou no fim. Um insert que falha no lote aparece num `write()` posterior ou na troca de fase. O `DeferredCommandError` liga a falha ao comando certo, como no aplicador.

   O output conta as linhas do texto renderizado em cada fase, como o `FileOutput` gravaria. A sessão numera os comandos a partir da linha em que o texto começa. No modo `hybrid`, a linha do relatório aponta para a linha do arquivo.

   O separador do PostgreSQL acompanha os `SET standard_conforming_strings` que encontra no texto. Como cada operação chega numa chamada separada, a sessão guarda o valor visto por último entre uma chamada e outra e volta ao valor do servidor depois do `RESET ALL`.

   Alternativa considerada: acumular a fase inteira e executar no fim dela. Rejeitada porque perde a falha antecipada e guarda a fase de carga inteira na memória.

   Custo: uma ida ao banco por comando, ou por lote quando a sessão agrupa (MongoDB), como no aplicador. Como geração e execução dividem a mesma thread, o tempo total tende a ficar perto de gerar e aplicar em sequência. O ganho é a falha antecipada e não precisar do `03` em disco no modo `direct`.

2. A sessão lê linhas, não caminhos.

   Hoje o `TargetSession.commands(path, dry_run)` abre o arquivo. O output direto tem o texto na memória. O contrato passa a ser `commands(lines, dry_run, first_line=1)`, sobre qualquer iterável de linhas. O aplicador passa o arquivo aberto e o output direto passa o texto renderizado. O separador do PostgreSQL já lê linha a linha (`split_sql`). O MongoDB lê uma operação por linha.

3. Transação única, com modos, políticas e capacidades iguais aos do aplicador.

   Os motivos são os do aplicador: ou a carga entra inteira, ou nada entra. O `finish(True)` termina em `commit` no modo `apply` e em `rollback` no `dry-run`. O `finish(False)`, ou um `close()` sem `finish(True)`, termina em `rollback`. Um `commit` que falha faz o `finish(True)` levantar exceção. O runner já grava a execução como `failed` nesse caso.

   As políticas e os padrões são os do aplicador. O padrão é `stop` no `apply`. No `dry-run`, é `continue` quando o destino tem savepoint. Com `stop`, o comando que falha faz o `write()` (ou a troca de fase, num lote adiado) levantar exceção, o runner chama `finish(False)` e o manifesto registra a posição do erro. Com `continue`, o comando que falha é desfeito por savepoint, vai para o relatório e a geração segue.

   As capacidades são conferidas pelo `check_capabilities()` de `core/application.py`, como no aplicador. Primeiro as declaradas pelo adapter, quando o output é montado e antes de qualquer conexão. Depois as da sessão, quando ela abre. Um destino sem transação recusa o output direto nos dois modos. Um MongoDB fora de replica set é recusado quando a sessão abre.

   Modos e políticas saem do `applier.py` para `core/apply/execution.py`, usado pelos dois. Os testes atuais do aplicador continuam passando sem mudança.

4. Segurar a fase `setup` até as leituras do destino fecharem.

   A factory abre `setup` ao montar o contexto, antes de o runner carregar os mapas de referência. Se os comandos dessa fase rodassem na hora, poderiam pegar locks em tabelas que o adapter lê em seguida pela conexão de leitura. O processo ficaria esperando por ele mesmo. Por isso o `DirectDatabaseOutput` guarda as operações de `setup` na memória e só as executa quando `prepare_target` abre. O runner só abre `prepare_target` depois do `close_target_reads()`.

   O core não escreve nada em `setup` hoje. Um cliente que escreva muito nessa fase gasta memória proporcional.

5. Sessão de escrita própria, aberta quando `prepare_target` abre.

   O output conecta um segundo adapter de destino com a mesma configuração `TARGET_*` do contexto e abre a sessão dele, como o `session_opener()` do aplicador já faz. Ele não usa o adapter de destino do contexto, que serve para renderizar e para os mapas de referência. A conexão só abre quando `prepare_target` abre, depois de as leituras fecharem. No PostgreSQL, o `close_reads()` termina a transação de leitura com `ROLLBACK` e guarda a lista de tabelas que a suspensão de triggers usa, então a conexão de escrita não espera lock de leitura e a renderização não consulta o destino.

   O `session()` do adapter de destino ganha `waits_between_commands=False`. O output direto pede `True`. Entre um comando e outro, a sessão espera o Python ler a origem e transformar as linhas. Um servidor PostgreSQL com `idle_in_transaction_session_timeout` derrubaria a sessão com a carga inteira. Com `True`, a sessão do PostgreSQL executa `SET idle_in_transaction_session_timeout = 0` no `begin()` e de novo depois de cada `RESET ALL`. O MongoDB não tem esse limite por sessão ociosa e ignora a opção.

   Alternativa considerada: passar a opção na conexão (`options='-c ...'`), que sobrevive ao `RESET ALL`. Rejeitada porque obrigaria o `connect()` de todo adapter a receber um parâmetro que só a sessão usa.

6. A cópia nativa entra pelo `include_file()`.

   O `include_file(path)` lê o arquivo do `pg_dump` com a sessão (`commands()` sobre o arquivo aberto, linha a linha, sem carregar o arquivo na memória), com as mesmas regras do aplicador: pula o `BEGIN` e o `COMMIT` em torno de large objects, os metacomandos, os parâmetros que o destino não conhece e os `setval` do `dry-run`. As linhas continuam a numeração da fase. A cópia nativa é a última coisa da fase `copy_compatible_entities`. A troca de fase executa o `end_artifact()` da sessão, que no PostgreSQL é o `RESET ALL` que desfaz o `search_path` vazio do dump.

7. Troca de fase.

   Ao abrir a fase seguinte, o output esvazia a política (`flush`, que roda os comandos adiados pela sessão), chama `end_artifact()` da sessão e fecha no relatório o arquivo da fase anterior. O nome de cada fase no relatório é o do arquivo que o `FileOutput` gravaria (`03_load_transformed_data.sql`, por exemplo), para que o relatório leia igual ao do aplicador.

8. Mesmo efeito do aplicador.

   Para as mesmas operações e o mesmo modo, o `DirectDatabaseOutput` executa os mesmos comandos, na mesma ordem e com os mesmos comandos pulados que o `ArtifactApplier` executaria sobre os arquivos. Os testes conferem isso com uma sessão falsa, comparando a lista de comandos executados pelos dois caminhos. O teste de ponta a ponta compara os relatórios.

   A diferença que sobra é o tempo de lock. No output direto, o `TRUNCATE` da fase `prepare_target` segura as tabelas durante toda a geração.

9. Relatório e manifesto.

   O output usa o mesmo `ApplicationReport`, em `aplicacoes/<YYYYmmdd-HHMMSS>-<modo>/`. O `relatorio.json` ganha a chave `source`, com `generation` no output direto e `artifacts` no aplicador.

   O `describe()` devolve, por exemplo, `{"type": "direct", "adapter": "postgres", "mode": "dry-run", "on_error": "continue", "report": "aplicacoes/20261126-101500-dry-run"}`. No `hybrid`, o manifesto mostra o `MirrorOutput` com as duas descrições.

   Com `continue`, uma execução que teve comandos com erro e terminou em `commit` fica `success` no manifesto. O relatório lista os erros. O `Migracao.py` mostra o mesmo resumo do aplicador: resultado, quantidade de erros e pasta do relatório.

10. Escolha do output pelo CLI.

    `--output file|direct|hybrid`. `hybrid` é `MirrorOutput(FileOutput, DirectDatabaseOutput)`, com o arquivo primeiro. Assim o arquivo recebe a operação que falhou no banco.

    `--apply` e `--dry-run` mantêm um significado só: como a transação termina no destino. Sem `--output`, ou com `file`, elas aplicam os arquivos depois da geração, como hoje. Com `direct` ou `hybrid`, definem o modo do output direto e nada é aplicado depois. `--output direct` sem `--apply` nem `--dry-run` é erro: não existe padrão que escreva no destino. O `--on-error` segue a mesma regra.

    A escolha não vai para o `.env`. Uma chave ali faria toda execução daquele cliente escrever no destino sem ninguém pedir na linha de comando.

    Para a escolha chegar à factory sem mudar o construtor dos clientes, o `Migracao.py` instancia o cliente dentro de `core.context.output_settings(settings)`, um gerenciador de contexto que guarda a escolha numa `ContextVar` lida pela factory. Fora dele, a factory usa `file`.

    Alternativa considerada: variável de ambiente. Rejeitada pelo mesmo motivo do `.env`: ela continuaria valendo nas próximas execuções do mesmo shell. Alternativa considerada: o construtor de cada cliente receber o output. Rejeitada porque obriga a mudar todos os clientes, inclusive os privados.

11. Limite de vida da transação no MongoDB.

    No MongoDB, a transação é desfeita pelo servidor quando passa do `transactionLifetimeLimitSeconds`, 60 segundos por padrão. Com o output direto, esse tempo inclui a geração inteira. A sessão já lê o limite e o grava no relatório pelo `describe()`. O output também o mostra no console ao abrir a sessão. A recomendação de aumentar o limite antes de uma carga vale com mais força aqui.

## Risks / Trade-offs

- Destino bloqueado durante toda a geração -> aceito, porque a carga já exige janela sem uso concorrente. A janela passa a incluir o tempo de geração.
- Conexão ociosa derrubada no meio de uma carga longa (túnel SSH, proxy, firewall) -> a transação é desfeita e a carga se perde no fim. O limite de sessão ociosa do PostgreSQL fica desligado na sessão de escrita, mas os outros limites dependem da rede. Ver Open Questions.
- Transação do MongoDB vencida pelo tempo de geração -> a carga se perde no fim. O limite aparece no console ao abrir a sessão e a documentação pede para aumentá-lo.
- Cliente que lê o destino pelo driver do adapter (`context.target.connection`) no meio da geração fica parado esperando o lock. O `read()` do adapter já recusa leituras depois do `close_reads()`, então isso só acontece fora do contrato -> documentar.
- Estouro do cache de subtransações com `continue` -> o mesmo do aplicador. Medir numa carga real.
- Erro perto do fim descarta a carga inteira -> é o preço da transação única, igual ao do aplicador.

## Migration Plan

1. Arquivar a `desacoplar-migracao-de-banco-e-versao`.
2. Extrair modos e políticas do `applier.py` para `core/apply/execution.py`, com os testes do aplicador sem mudança.
3. Mudar `TargetSession.commands()` para linhas e acrescentar `waits_between_commands` ao `session()`, nos adapters PostgreSQL e MongoDB.
4. Criar o `DirectDatabaseOutput` com testes usando sessão falsa.
5. Montar o output na factory, criar `output_settings()` e a opção `--output` no `Migracao.py`.
6. Validar com a AcmeEdu: `hybrid` com `--dry-run` e `direct` com `--apply` contra o PostgreSQL, comparando com o `Aplicar.py` sobre os mesmos arquivos. Depois, `direct` com `--apply` contra o MongoDB da matriz.
7. Atualizar a documentação.

Rollback: reverter os commits. O padrão `file` e o `Aplicar.py` não mudam de comportamento.

## Open Questions

- Vale manter `direct` sem arquivos ou só oferecer `hybrid`? Sem arquivos não dá para auditar nem reaplicar os comandos depois. O ganho é o espaço em disco do `03`.
- O túnel SSH usado para chegar ao destino (`docs/execucao.md`) derruba conexão ociosa? Se derrubar, o `direct` só deve ser usado de dentro da rede do destino. Precisa medir com uma geração longa antes de recomendar.
- O ganho compensa? Sem paralelismo, o tempo total fica perto de gerar e aplicar em sequência. A falha antecipada e o disco são os ganhos certos. O tempo precisa ser medido numa carga real.
- `--debug` com `--output direct --apply` grava uma carga parcial no destino. Essa combinação deve ser recusada? Hoje `--debug --apply` sobre arquivos já é permitido.
