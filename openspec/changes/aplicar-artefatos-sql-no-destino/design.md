## Context

A geração grava os comandos em `runs/<cliente>/<timestamp>/sql/`, em cinco arquivos: `00_setup`, `01_prepare_target`, `02_dump_compatible_tables` (gravado pelo `pg_dump`), `03_load_transformed_data` e `04_finalize_target`. O `rodar2.sh` aplica cada arquivo com `psql -a -f`, numa sessão separada, em autocommit e sem `ON_ERROR_STOP`. Depois filtra `ERROR:` e `WARNING:` dos logs para `erros/`.

O fluxo de `docs/execucao.md` separa geração e aplicação: os arquivos são compactados e enviados ao servidor antes de rodar. Quando a carga falha, o destino é recriado por `dropdb`, `createdb` e `pg_restore` do dump de homologação.

O item 2 de `docs/melhorias-futuras.md` recomendava um `DirectDatabaseOutput(conexao, mode='apply' | 'rollback')`, que executaria os comandos durante a geração. A change `introduzir-mirror-output` preparou o contrato para isso com `finish(success)` e `describe()`.

## Goals / Non-Goals

**Goals:**

- Aplicar uma execução inteira em transação única: ou tudo entra, ou nada entra.
- Ter um dry run que roda os mesmos comandos no destino e não deixa alteração.
- Registrar cada erro com arquivo, posição do comando, SQLSTATE e mensagem, sem depender de `grep` em log.
- Aplicar a pasta de uma execução já gerada, inclusive em outra máquina.

**Non-Goals:**

- Não executar SQL durante a geração. O contrato `OutputAdapter` não muda.
- Não otimizar a carga com `COPY` ou lotes num mesmo `execute`. O desempenho fica igual ao do `psql` atual, e otimização é assunto do item 11 do roadmap.
- Não remover o `rodar2.sh` nesta change.
- Não corrigir o SQL gerado. Erros nos arquivos aparecem no relatório como apareceriam no log do `psql`.

## Decisions

1. Aplicar os artefatos depois da geração, não durante.

   O `load_reference_map()` consulta o destino pela conexão do `PostgresMetadataProvider` enquanto os inserts são gerados. Hoje essa leitura vê o destino como estava antes da carga, porque nada é aplicado até a geração terminar. Se os comandos fossem executados durante a geração, haveria dois caminhos, ambos ruins:

   - Na mesma conexão do output, a leitura enxerga o que a transação já fez. Uma tabela truncada pelo `01_prepare_target` aparece vazia e o mapa sai diferente do atual.
   - Numa conexão separada, ler uma tabela truncada espera o lock `ACCESS EXCLUSIVE` do `TRUNCATE`, que só sai no fim da transação. O processo fica esperando por ele mesmo.

   Dois fatos reforçam a escolha. O `02_dump_compatible_tables.sql` é gravado pelo `pg_dump` fora do output, e o `SqlFileOutput` apagaria esse arquivo se a fase fosse aberta por ele. E o fluxo de execução já envia os arquivos para outra máquina antes de aplicar. Um aplicador que recebe a pasta da execução inclui o dump pela ordem dos arquivos e funciona onde os arquivos estiverem.

   Alternativa considerada: o `DirectDatabaseOutput` do roadmap. Fica adiado até a geração parar de ler o destino no meio da carga. O item 7 do roadmap (padronizar lookups) pode carregar os mapas antes da primeira fase e destravar essa opção.

2. Uma transação para a aplicação inteira.

   Os arquivos rodam em ordem na mesma transação. No modo `apply`, o aplicador termina em `COMMIT`. No `dry-run`, termina sempre em `ROLLBACK`. Qualquer interrupção antes do fim, inclusive do processo, deixa o destino como estava.

   Alternativa considerada: commit por arquivo ou por lote, como sugeria o roadmap. Rejeitada porque volta a deixar o destino carregado pela metade quando algo falha.

   Custo: o `TRUNCATE` segura `ACCESS EXCLUSIVE` até o fim, e um erro perto do final descarta a carga inteira. Hoje um erro também obriga a recomeçar, só que depois de restaurar o banco.

3. Executar com `psycopg2`, não com `psql`.

   O `psql --single-transaction` não protege contra um `COMMIT` dentro do script, como avisa a própria documentação do `psql`. O `ON_ERROR_ROLLBACK` cria um savepoint por comando. E o relatório continuaria dependendo de filtrar log. Em Python o aplicador decide o que fazer com cada comando.

   O aplicador executa um comando por `execute()`, o mesmo número de idas ao banco que o `psql -f` faz hoje. Os arquivos são lidos em streaming, sem carregar tudo na memória.

4. Separar comandos pelas regras léxicas do PostgreSQL.

   O separador considera strings `'...'` com `''`, strings `E'...'` com escape por barra invertida, identificadores `"..."`, dollar quotes `$tag$...$tag$`, comentários `--` e comentários `/* */` aninhados. Um `;` fora disso encerra o comando. Texto sem `;` no fim do arquivo vira o último comando, como no `psql`.

   Uma linha que começa com `\` no início de um comando é metacomando do `psql`. O aplicador pula e conta. As versões atuais do `pg_dump` gravam `\restrict` e `\unrestrict` em dumps de texto. Um `COPY ... FROM stdin` falha com mensagem clara, porque os dados viriam no formato do `psql`. O dump atual usa `--column-inserts` e não gera `COPY`.

   O separador reproduz o lexer também quando o SQL está errado. Uma string `E'...'` que termina em barra invertida engole o texto seguinte, como acontece no `psql`.

   O separador acompanha os `SET standard_conforming_strings` do arquivo. Com a configuração desligada, a barra invertida escapa a aspa também em `'...'`, e o servidor lê a string assim. O `pg_dump` grava esse `SET` com o valor da origem. O valor inicial é `on`, o padrão do PostgreSQL desde a 9.1.

   A separação foi comparada com o `psql` 16 em 23 entradas, incluindo um trecho no formato do `pg_dump` e INSERTs gerados pelo core. Os comandos enviados ao servidor coincidiram em todas. A diferença conhecida é a barra invertida no meio de um comando: o `psql` executa ali um metacomando e o separador manda o texto ao servidor, que recusa. Nem o core nem o `pg_dump` geram isso.

5. Tratar no aplicador o que o `psql` resolvia sozinho.

   - Controle de transação: `BEGIN`, `START TRANSACTION`, `COMMIT`, `END`, `ROLLBACK` e `ABORT` vindos dos arquivos são pulados e contados. O `pg_dump` em texto envolve os large objects em `BEGIN`/`COMMIT`, e esse `COMMIT` confirmaria o dry run no meio do caminho.
   - Configurações de sessão: depois de cada arquivo o aplicador executa `RESET ALL`. O dump roda `SELECT pg_catalog.set_config('search_path', '', false)` e vários `SET`. No `rodar2.sh` cada arquivo era uma sessão e essas configurações morriam com ela. Numa transação única elas alcançariam o `03` e o `04`, e nomes de tabela sem schema deixariam de resolver.
   - Sequences no dry run: `setval` não é desfeito pelo `ROLLBACK`, então o dry run pula esses comandos e conta. O `nextval` dos defaults de coluna continua avançando sequences durante o dry run. Isso não atrapalha a carga real, porque o `04_finalize_target` e o dump ajustam as sequences por `setval`.
   - Sequences num `apply` que falha: os `setval` que rodaram antes do erro continuam valendo depois do `ROLLBACK`. No teste contra o PostgreSQL 16, um `apply` com `stop` que falhou no `03` deixou as tabelas vazias e a sequence de `a` em 4, o valor gravado pelo dump. Adiar os `setval` para o fim mudaria a ordem da carga: os INSERTs do `03` que usam o default da coluna pegariam ids já usados pelo dump. O efeito fica aceito, porque a próxima carga roda os mesmos `setval` e uma sequence adiantada só deixa buraco na numeração.
   - Comandos que não rodam em transação, como `VACUUM` e `CREATE INDEX CONCURRENTLY`, falham e aparecem no relatório. O core não gera esses comandos, mas o SQL próprio de um cliente pode gerar.

6. Política de erro `stop` ou `continue`.

   Com `stop`, o primeiro erro encerra a aplicação com `ROLLBACK` e o relatório aponta o comando. Com `continue`, o aplicador abre um savepoint a cada lote de comandos (1000 por padrão). Se o lote falha, volta ao savepoint e reexecuta o lote comando a comando, cada um com seu savepoint. Os comandos que falharem vão para o relatório e os demais ficam aplicados.

   Padrões: `stop` no `apply` e `continue` no `dry-run`, para que a validação liste todos os erros de uma vez. `apply` com `continue` reproduz o comportamento atual do `psql`, que pula o comando com erro e segue.

   Correção do roadmap: o savepoint por lote não mantém a transação abaixo de 64 subtransações. Uma subtransação que gravou dados continua na lista da transação até o fim, mesmo depois do `RELEASE SAVEPOINT`. Uma carga de 1 milhão de comandos em lotes de 1000 cria 1000 subtransações. O estouro do cache deixa outras sessões e réplicas mais lentas. Com o destino isolado isso é aceitável, mas precisa ser medido numa carga real antes de usar `continue` num banco com réplica.

7. Um relatório por aplicação, fora do manifesto da geração.

   Cada aplicação cria `aplicacoes/<YYYYmmdd-HHMMSS>-<apply|dry-run>/` dentro da pasta da execução, com:

   - `relatorio.json`: modo, política de erro, destino (host, porta, banco e usuário, sem senha), início, fim, resultado (`committed`, `rolled_back` ou `failed`) e, por arquivo, comandos executados, comandos com erro, comandos pulados por motivo e duração;
   - `erros.txt`: para cada erro, arquivo, número do comando, linha inicial, SQLSTATE, mensagem e o comando, truncado.

   Alternativa considerada: gravar a aplicação no `manifest.json`. Rejeitada porque uma mesma execução pode ser aplicada mais de uma vez (dry run e depois carga), e o manifesto é reescrito inteiro a cada gravação. Com uma pasta por aplicação, o modo fica no nome da pasta e no relatório, e uma validação não se confunde com uma carga.

8. Recusar execuções incompletas.

   O aplicador lê o `manifest.json` antes de conectar. Ele recusa a execução se o status não for `success` ou se faltar algum arquivo esperado. O `02_dump_compatible_tables.sql` só é exigido quando a execução não é debug, já que o modo debug não roda `pg_dump`.

9. Conexão com o destino.

   O `Aplicar.py` usa as variáveis padrão da libpq (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`), como o `rodar2.sh`, porque no servidor pode não existir o `.env` do cliente. Ao contrário do `rodar2.sh`, ele não assume valores padrão: exige `PGHOST` e `PGDATABASE` e mostra o destino antes de começar. O `Migracao.py --apply` usa as chaves `PG_V2_*` do `.env`, o mesmo destino que a geração consulta. A conexão é aberta com `client_encoding` UTF8, a codificação dos arquivos gerados e do `pg_dump -E utf-8`.

## Risks / Trade-offs

- Destino bloqueado durante toda a aplicação -> aceito, porque a carga já exige janela sem uso concorrente.
- O separador divergir do lexer do PostgreSQL e partir um comando no lugar errado -> testes com trechos no formato do `pg_dump` e com o SQL gerado pelo core. Aspas não fechadas no fim do arquivo encerram a aplicação com erro claro.
- Estouro do cache de subtransações com `continue` -> ver decisão 6. Medir em carga real.
- Um comando por ida ao banco fica lento em rede com latência alta -> é o mesmo custo do `psql` hoje.
- Erro no fim de uma carga longa descarta tudo -> é o preço da transação única. O dry run em cópia descartável continua sendo a validação recomendada antes da carga completa.

## Migration Plan

1. Criar o separador de comandos com testes.
2. Criar o aplicador e o relatório com testes usando conexão falsa.
3. Criar o `Aplicar.py` e as opções `--apply` e `--dry-run` no `Migracao.py`.
4. Validar dry run e apply contra o `target-db` do Docker com uma execução gerada.
5. Atualizar a documentação.

Rollback: remover o aplicador e voltar a usar só o `rodar2.sh`. Nada muda nos arquivos gerados.

## Open Questions

- Respondida em 2026-11-24: o `apply` usa `stop` como padrão. Quem precisar do comportamento do `psql` passa `--on-error continue`.
- O servidor que recebe o zip tem Python com `psycopg2`? Se não tiver, o aplicador roda de uma máquina com acesso ao destino e o `rodar2.sh` continua para o servidor.
