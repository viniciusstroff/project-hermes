# Execução

## Ambiente

### Dependências Python

```bash
pip install -r requirements.txt -r requirements/postgres.txt -r requirements/firebird.txt -r requirements/mongodb.txt
```

O `requirements.txt` tem só o core (`python-dotenv`). Os drivers ficam em `requirements/<adapter>.txt`: `psycopg2-binary` no `postgres.txt`, `firebirdsql` no `firebird.txt` e `pymongo` no `mongodb.txt`. Instale só os dos adapters que o cliente usa. O driver de cada banco só é importado quando uma conexão daquele adapter abre.

### Versões dos servidores

Cada adapter lê a versão do servidor ao conectar e confere a faixa que aceita. Fora dela, a execução para antes da geração, com o adapter, a versão encontrada e a mínima na mensagem. A versão de cada conexão vai para o `manifest.json`.

| Adapter | Origem | Destino |
|---|---|---|
| `postgres` | 9.6 ou mais novo | 12 ou mais novo |
| `firebird` | 2.5 ou mais novo | não se aplica |
| `mongodb` | 4.4 ou mais novo | 4.4 ou mais novo |

### MongoDB

O adapter `mongodb` lê `<PREFIXO>_URI`, obrigatória e fora do manifesto. O banco vem de `<PREFIXO>_NAME` ou da própria URI. Na origem, cada entidade é uma coleção e o `_id` só vem quando é pedido. Os artefatos do destino são `.jsonl`, uma operação por linha em Extended JSON relaxado, que guarda data, `Decimal128` e binário.

A aplicação no MongoDB roda numa transação, que só existe em replica set (ou atrás de um `mongos`). Num servidor solto, o aplicador recusa o apply e o dry run antes de executar qualquer comando. O MongoDB não tem savepoint, então a política `continue` é recusada e o dry run usa `stop`. Os inserts seguidos na mesma coleção vão em lotes de 1000 (`insert_many`). Um erro no lote aparece no relatório com o número e a linha do comando que falhou.

O servidor desfaz uma transação mais velha que o `transactionLifetimeLimitSeconds`, 60 segundos por padrão. O valor vai para o relatório da aplicação. Para uma carga grande, aumente o limite antes de aplicar, por exemplo com `db.adminCommand({setParameter: 1, transactionLifetimeLimitSeconds: 3600})`.

Um `pg_dump` mais novo que o destino grava parâmetros que o destino não conhece, como o `SET transaction_timeout` do 17. Na aplicação, a sessão PostgreSQL lê os parâmetros do servidor e pula esses comandos, contados como `unknown_setting` no relatório.

### Conectividade SSH

A V1 geralmente está em um servidor remoto. Abrir os tunnels necessários antes de rodar:

```bash
ssh -L 172.17.0.1:3050:10.0.2.XXX:3050 \
    -L 172.17.0.1:3396:10.0.2.XXX:5432 \
    usuario@servidor
```

- Porta `3050` → Firebird V1
- `3396` (ou a configurada no `.env`) → PostgreSQL V1

O Hermes só abre as conexões declaradas no `.env` do cliente. Um cliente sem origem Firebird não declara nenhum `SOURCE_<NOME>_ADAPTER=firebird`. O túnel da porta 3050 deixa de ser necessário. Pedir `context.source('fb')` sem essa origem declarada gera um erro com o nome pedido e a lista das origens que existem.

---

## Rodando a migração

### Via entry point unificado (recomendado)

```bash
# Migração completa
python3 Migracao.py Acme

# Modo debug: 100 registros por consulta, inclusive na cópia das entidades compatíveis
python3 Migracao.py Acme --debug 100

# Debug com quantidade diferente
python3 Migracao.py Acme --debug 500

# Gera e valida no destino, sem deixar alteração
python3 Migracao.py Acme --dry-run

# Gera e aplica no destino
python3 Migracao.py Acme --apply

# Grava os arquivos e valida no destino durante a geração
python3 Migracao.py Acme --output hybrid --dry-run

# Carrega no destino durante a geração, sem arquivos de comandos
python3 Migracao.py Acme --output direct --apply
```

Com `--apply` ou `--dry-run`, o `Migracao.py` aplica a execução que acabou de gerar no banco das chaves `TARGET_*` do `.env`, o mesmo que a geração consulta. A aplicação só começa se a geração terminou com status `success`. Ela funciona como o `Aplicar.py`, descrito em [Aplicando os SQLs no destino](#aplicando-os-sqls-no-destino). O `--on-error` também vale aqui.

Com `--output direct` ou `hybrid`, os comandos rodam no destino durante a geração e nada é aplicado depois. Ver [Executando no destino durante a geração](#executando-no-destino-durante-a-geração).

### Via Docker

```bash
# App + PostgreSQL local de destino
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d

# Migração completa dentro do container
docker exec -it project-hermes python3 Migracao.py Acme

# Debug
docker exec -it project-hermes python3 Migracao.py Acme --debug 100

# Debug com dry run no target-db
docker exec -it project-hermes python3 Migracao.py Acme --debug 100 --dry-run
```

Por padrão, a imagem instala os drivers de todos os adapters e não traz o `pg_dump`. Quem usa a cópia nativa (`SOURCE_<NOME>_NATIVE_COPY=pg_dump`) constrói a imagem com o cliente de uma versão principal:

```bash
PG_CLIENT_VERSION=17 docker compose build
```

O `pg_dump` precisa ser da versão do servidor de origem ou mais novo. Um cliente novo exporta de servidores antigos: o 17 exporta de um 9.6. A conferência é feita ao conectar.

O contrato Docker do destino usa o hostname interno `target-db` na rede `hermes-net`.
Para alterar a porta exposta no host sem mudar o contrato interno, ajuste `TARGET_DB_PUBLISHED_PORT`.
Por exemplo, `TARGET_DB_PUBLISHED_PORT=5434` publica o PostgreSQL como `localhost:5434`,
mas o container da aplicação continua acessando `target-db:5432`.


## Arquivos gerados

Após a execução, os artefatos ficam em `runs/<cliente>/<YYYYmmdd-HHMMSS>/`:

| Arquivo | Função |
|---|---|
| `manifest.json` | Metadados da execução: modo debug, output, conexões sem senha com a versão de cada servidor e a lista de artefatos com fase, caminho e formato |
| `logs/migration.log` | Log da geração feita pelo Python |
| `logs/migration.jsonl` | O mesmo log em JSON Lines, um evento por linha, com fase, unidade e nível |
| `relatorio-geracao.json` | Linhas lidas, inserts, duração e perdas de dado de cada unidade e de cada fase |
| `comandos/00_setup.sql` | Arquivo inicial da execução |
| `comandos/01_prepare_target.sql` | Limpeza da V2 e desativação de triggers |
| `comandos/02_copy_compatible_entities.sql` | Cópia das entidades de `compatible_entities()`, linha a linha ou pelo `pg_dump` |
| `comandos/03_load_transformed_data.sql` | `INSERT`s principais |
| `comandos/04_finalize_target.sql` | `setval` e reativação de triggers |
| `aplicacoes/<YYYYmmdd-HHMMSS>-<modo>/` | Relatório de cada aplicação no destino, criado pelo `Aplicar.py` |

A extensão dos arquivos de `comandos/` vem do adapter de destino. Com destino PostgreSQL, ela é `.sql`.

### Relatório da geração

Cada `TableMigration`, `ExpandMigration`, `MultiTargetMigration` e entidade copiada linha a linha é uma unidade. O `relatorio-geracao.json` traz, para cada unidade, a origem, a fase, as linhas lidas, os inserts por entidade de destino, a duração e a situação (`done` ou `interrupted`). Por fase, traz a duração e os totais. Uma cópia com `pg_dump` aparece na fase `copy_compatible_entities` com as entidades, a duração e o tamanho do arquivo. No fim vem o tamanho de cada artefato de `comandos/`. Numa execução que falhou, o relatório traz também a mensagem do erro.

O relatório é gravado no sucesso e na falha, antes do `manifest.json`. Uma falha no meio de uma unidade deixa essa unidade como `interrupted`, com as linhas lidas até ali.

O relatório também conta as perdas de dado: valores da origem que uma estratégia trocou por `NULL` ou pelo `fallback`. Hoje contam três estratégias:

| Estratégia | Motivo | Quando conta |
|---|---|---|
| `Lookup` | `lookup_miss` | a chave não está no mapa |
| `DateConvert` | `invalid_date` | o texto não segue o formato, como `31/02/2001` |
| `EmailExtract` | `email_not_found` | o texto não tem e-mail |

Valor nulo ou vazio na origem não conta: a origem já não tinha o dado. Cada grupo de perda (entidade, coluna, estratégia e motivo) guarda o total e até 20 valores originais de exemplo, com até 100 caracteres cada. Esses exemplos são dados da origem, como datas de nascimento e trechos de texto livre. O relatório tem a mesma sensibilidade dos arquivos de `comandos/`.

O fim do `migration.log` resume a execução, com uma linha por unidade e um aviso por grupo de perda:

```text
[26/11/2026 16:08:50] ----- Resumo da geração -----
[26/11/2026 16:08:50] students (v1): 1500 linhas lidas, 4580 inserts, 0.26 s
[26/11/2026 16:08:50] enrollments (v1): 6000 linhas lidas, 6000 inserts, 0.43 s
[26/11/2026 16:08:50] [AVISO] people.birth_date: 50 datas inválidas (ex.: '31/02/2001')
```

O `migration.jsonl` tem uma linha JSON por evento, com `ts`, `run_id`, `level` (`info`, `warning` ou `error`), `phase`, `unit`, `event`, `message` e `data`. Cada linha do `migration.log` vira um evento `log`. As métricas acrescentam `phase_started`, `phase_finished`, `unit_started`, `unit_finished` e `data_loss`. Para ver só os erros de uma execução:

```bash
jq -c 'select(.level == "error")' runs/AcmeEdu/20261126-160849/logs/migration.jsonl
```

---

## Aplicando os SQLs no destino

O `Aplicar.py` aplica os artefatos de uma execução já gerada, na ordem das fases e numa única transação. Ou a carga inteira entra, ou nada entra.

```bash
export TARGET_HOST=localhost TARGET_PORT=5432 TARGET_NAME=tenant TARGET_USER=tenant TARGET_PASS=tenant

# Valida: executa tudo e termina em ROLLBACK
python3 Aplicar.py runs/Acme/20261124-153000 --dry-run

# Aplica: termina em COMMIT se nenhum comando falhar
python3 Aplicar.py runs/Acme/20261124-153000
```

Dentro do container, o destino local é o `target-db` e as variáveis vão pelo `docker exec`:

```bash
docker exec -it -e TARGET_HOST=target-db -e TARGET_NAME=tenant -e TARGET_USER=tenant -e TARGET_PASS=tenant \
    project-hermes python3 Aplicar.py runs/Acme/20261124-153000 --dry-run
```

O adapter de destino e a lista de artefatos vêm do `manifest.json` da execução. A conexão vem das variáveis `TARGET_*`, com os mesmos nomes do `.env` do cliente. O adapter diz quais são obrigatórias: no PostgreSQL, `TARGET_HOST`, `TARGET_NAME` e `TARGET_USER`. Um `TARGET_ADAPTER` diferente do manifesto é recusado. As variáveis da libpq (`PGHOST` e as outras) não são lidas. Antes de conectar, o script mostra o destino e o modo.

O fluxo recomendado é rodar o dry run, ler os erros e só depois aplicar.

### Modos

| Modo | Fim da transação | Política de erro padrão |
|---|---|---|
| `apply` (padrão) | `COMMIT` se a aplicação chegar ao fim, `ROLLBACK` se falhar | `stop` |
| `dry-run` | sempre `ROLLBACK` | `continue` se o destino tem savepoint, senão `stop` |

O aplicador confere as capacidades do adapter antes de conectar e de novo na sessão aberta. Os dois modos precisam de transação. A política `continue` precisa de savepoint. Se o destino não tem a capacidade, a aplicação é recusada com o nome dela na mensagem. O PostgreSQL tem as duas.

### Políticas de erro

Com `--on-error stop`, o primeiro comando com erro desfaz tudo. O relatório aponta o arquivo, a posição do comando e a mensagem do banco.

Com `--on-error continue`, só os comandos que falharam são desfeitos. Cada erro vai para o relatório e a aplicação segue. No dry run isso mostra todos os erros de uma vez. No `apply`, a carga é confirmada sem as linhas que falharam, então use com cuidado.

O `continue` agrupa os comandos em lotes de 1000 com um savepoint por lote. Quando um comando falha, o lote volta ao savepoint e é reexecutado comando a comando.

### O que o adapter PostgreSQL não executa

As regras abaixo ficam na sessão do adapter PostgreSQL (`core/adapters/postgres_session.py`). Outro adapter tem as próprias.

- metacomandos do `psql`, como `\restrict` e `\unrestrict`, que o `pg_dump` grava;
- `BEGIN`, `COMMIT` e os outros comandos de controle de transação, que o `pg_dump` grava em volta de large objects;
- no dry run, os `SELECT ... setval(...)`, porque o `ROLLBACK` não desfaz uma sequence.

Cada comando pulado aparece contado no relatório. Um `COPY ... FROM stdin` encerra a aplicação com erro, porque os dados viriam no formato do `psql`. O dump atual usa `--column-inserts` e não gera `COPY`.

Depois de cada arquivo a sessão PostgreSQL executa `RESET ALL`. Assim o `search_path` vazio que o `pg_dump` configura não chega ao `03` nem ao `04`, como acontecia quando cada arquivo rodava numa sessão de `psql`.

### Relatório

Cada aplicação cria uma pasta dentro da execução. O `manifest.json` e os arquivos de `comandos/` não mudam.

```text
runs/Acme/20261124-153000/aplicacoes/
  20261124-160000-dry-run/
    relatorio.json
    erros.txt
  20261124-161500-apply/
    relatorio.json
    erros.txt
```

O `relatorio.json` traz modo, política de erro, adapter e destino sem senha, início, fim, resultado (`committed`, `rolled_back` ou `failed`) e o motivo da falha. Por arquivo, traz comandos executados, comandos com erro, comandos pulados por motivo e duração.

O `erros.txt` tem um bloco por comando que falhou, com arquivo, número do comando, linha inicial, código do erro dado pelo adapter (o `SQLSTATE` no PostgreSQL), mensagem e o comando truncado em 500 caracteres. Ele é gravado à medida que os erros acontecem, então dá para acompanhar uma carga longa:

```text
== 03_load_transformed_data.sql, comando 2, linha 2, código 23505
duplicate key value violates unique constraint "b_pkey"
DETAIL:  Key (id)=(1) already exists.
-- comando:
INSERT INTO b VALUES (1, 'duplicado')
```

Durante a aplicação, o terminal mostra o início de cada arquivo, uma linha a cada 50 mil comandos e as contagens no fim de cada arquivo.

### Código de saída

| Código | Situação |
|---|---|
| 0 | terminou em `COMMIT` ou, no dry run, em `ROLLBACK` |
| 1 | a aplicação falhou, a execução estava incompleta, o destino não tem a capacidade pedida ou recusou a conexão |
| 2 | faltou uma variável `TARGET_*` obrigatória, o `TARGET_ADAPTER` difere do manifesto ou algum argumento é inválido |
| 130 | interrompida com Ctrl+C; a transação foi desfeita |

Um dry run com erros termina com código 0. Confira a linha `Comandos com erro` no fim da saída ou o `erros.txt`.

### Cuidados

- O destino fica bloqueado durante toda a aplicação, porque o `TRUNCATE` segura o lock até o fim da transação. Rode só em janela sem uso concorrente, como já acontece com a carga.
- O aplicador recusa execuções com status diferente de `success`, sem o adapter de destino no manifesto, com algum artefato listado ausente ou com formato diferente do adapter. No modo debug o `02_copy_compatible_entities.sql` também existe, com no máximo o limite de linhas por entidade.
- Num `apply` que falha, os `setval` que já rodaram continuam valendo, porque sequences não voltam no `ROLLBACK`. As tabelas voltam ao estado anterior e a próxima carga ajusta as sequences de novo.
- No dry run, o `nextval` dos defaults de coluna avança as sequences. A carga real corrige isso pelos `setval` do `04` e também pelos do `pg_dump` quando a cópia nativa está ligada.
- Um erro perto do fim descarta a carga inteira. Em cargas longas, rode o dry run antes, de preferência numa cópia do destino.

---

## Executando no destino durante a geração

O `--output` escolhe para onde vão os comandos:

| `--output` | Arquivos em `comandos/` | Comandos no destino |
|---|---|---|
| `file` (padrão) | sim | depois da geração, só com `--apply` ou `--dry-run` |
| `direct` | não | durante a geração |
| `hybrid` | sim | durante a geração |

`--apply` e `--dry-run` dizem como a transação termina no destino, em qualquer output. Com `file`, elas aplicam os arquivos depois da geração, como o `Aplicar.py`. Com `direct` ou `hybrid`, elas são obrigatórias: não existe padrão que escreva no destino sem ninguém pedir. O `--on-error` vale nos três, com os mesmos padrões do aplicador. A escolha não vai para o `.env`, para que uma execução nunca escreva no destino sem estar na linha de comando.

```bash
# Valida contra o destino e grava os arquivos para auditoria
python3 Migracao.py AcmeEdu --output hybrid --dry-run

# Mesma validação, parando no primeiro erro
python3 Migracao.py AcmeEdu --output hybrid --dry-run --on-error stop

# Carga, sem os arquivos de comandos
python3 Migracao.py AcmeEdu --output direct --apply
```

Cada operação vai para o destino antes de a geração seguir, pela mesma sessão do adapter que o `Aplicar.py` usa, com as mesmas regras de comandos pulados. Numa transação só: no `apply` ela termina em `COMMIT`, no `dry-run` em `ROLLBACK`. Numa falha ou interrupção, nada fica no destino. Com `stop`, o primeiro comando com erro derruba a geração na hora, com a fase, o número do comando e a linha que ele teria no arquivo. O relatório vai para `aplicacoes/<timestamp>-<modo>/`, como no aplicador, com `"source": "generation"` no `relatorio.json`. O manifesto descreve o output com o modo, a política e a pasta do relatório.

A sessão de escrita usa uma conexão própria, aberta só quando a fase `prepare_target` começa, depois de os mapas de referência carregarem. No PostgreSQL, ela desliga o `idle_in_transaction_session_timeout` da sessão, porque passa a geração inteira esperando o Python entre um comando e outro.

### Quando usar

- `hybrid --dry-run` mostra os erros contra o destino numa execução só e ainda deixa os arquivos para auditar ou aplicar depois.
- `direct --apply` evita gravar o `03_load_transformed_data` em disco, que num cliente grande ocupa gigabytes. Sem arquivos, não dá para auditar nem reaplicar a carga depois. Para isso, use `hybrid`.
- O tempo não cai. Na AcmeEdu, num PostgreSQL local, `direct --apply` levou 5,2 s e gerar e aplicar em sequência levou 3,7 s. O ganho é a falha antecipada e o disco, não a velocidade.

### Cuidados

- O destino fica bloqueado pelo `TRUNCATE` desde a fase `prepare_target` até o fim da geração, não só durante a aplicação. A janela sem uso concorrente passa a incluir o tempo de geração.
- Uma conexão ociosa derrubada pela rede (túnel SSH, proxy, firewall) desfaz a carga inteira no fim. Ainda não foi medido se o túnel usado para chegar ao destino faz isso numa geração longa. Até lá, prefira rodar de dentro da rede do destino.
- No MongoDB, o servidor desfaz uma transação mais velha que o `transactionLifetimeLimitSeconds`, 60 segundos por padrão. Com o output direto, esse tempo cobre a geração inteira. O limite aparece no console quando a sessão abre e no relatório. Aumente o parâmetro antes de uma carga.
- O script do cliente não deve ler o destino pelo driver (`context.target.connection`) no meio da geração: a leitura espera o lock da conexão de escrita. O `read()` do adapter já recusa leituras depois que os mapas carregam.
- `--debug` com `--output direct --apply` grava uma carga parcial no destino, como `--debug --apply` sobre arquivos.
- Um `Native` sem `;` no fim roda sozinho no output direto. No arquivo, ele se junta ao comando seguinte. Termine os comandos de um `Native` com `;`.

---

## Resetar o banco V2 local antes de reexecutar

Útil para simular o ambiente de produção a partir de um dump de homologação. Com o `Aplicar.py`, uma carga que falha não deixa dados no destino. O reset só é preciso para refazer uma carga confirmada.

```bash
dropdb -h localhost -p 4003 -U tenant-demo tenant-demo
createdb -h localhost -p 4003 -U tenant-demo tenant-demo
pg_restore -h localhost -p 4003 -U tenant-demo -W -F t -d tenant-demo --no-owner --no-privileges ../acme_database.tar
```


## Debug com breakpoint (VS Code + Docker)

```bash
docker exec -it project-hermes python3 -m debugpy --listen 0.0.0.0:5678 --wait-for-client Migracao.py Acme
```

Em seguida pressione `F5` no VS Code (configuração `launch.json` já existente no projeto).

---

## Transferindo arquivos para o servidor

```bash
# Enviando as queries geradas (compactadas)
scp "$RUN_DIR"/Acme.zip seu_usuario@servidor:

# Obtendo os logs de volta
scp seu_usuario@servidor-demo:/tmp/Acme_logs.zip .
```

Para rodar o `Aplicar.py` no servidor, ele precisa de Python 3 com o driver do destino (`requirements/<adapter>.txt`) e de uma cópia do `Aplicar.py` e da pasta `core/`. A pasta da execução pode vir de outra máquina, porque o aplicador resolve os arquivos a partir dela e não pelos caminhos do manifesto. Se o servidor não tiver Python, rode o `Aplicar.py` de uma máquina com acesso ao destino.
