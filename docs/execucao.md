# Execução

## Ambiente

### Dependências Python

```bash
pip install -r requirements.txt
```

Pacotes: `firebirdsql`, `psycopg2-binary`, `python-dotenv`.

### Conectividade SSH

A V1 geralmente está em um servidor remoto. Abrir os tunnels necessários antes de rodar:

```bash
ssh -L 172.17.0.1:3050:10.0.2.XXX:3050 \
    -L 172.17.0.1:3396:10.0.2.XXX:5432 \
    usuario@servidor
```

- Porta `3050` → Firebird V1
- `3396` (ou a configurada no `.env`) → PostgreSQL V1

---

## Rodando a migração

### Via entry point unificado (recomendado)

```bash
# Migração completa: pg_dump + geração de SQL
python3 Migracao.py Acme

# Modo debug: 100 registros por tabela, sem pg_dump
python3 Migracao.py Acme --debug 100

# Debug com quantidade diferente
python3 Migracao.py Acme --debug 500

# Gera e valida no destino, sem deixar alteração
python3 Migracao.py Acme --dry-run

# Gera e aplica no destino
python3 Migracao.py Acme --apply
```

Com `--apply` ou `--dry-run`, o `Migracao.py` aplica a execução que acabou de gerar no banco das chaves `PG_V2_*` do `.env`, o mesmo que a geração consulta. A aplicação só começa se a geração terminou com status `success`. Ela funciona como o `Aplicar.py`, descrito em [Aplicando os SQLs no destino](#aplicando-os-sqls-no-destino). O `--on-error` também vale aqui.

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

O contrato Docker do destino usa o hostname interno `target-db` na rede `hermes-net`.
Para alterar a porta exposta no host sem mudar o contrato interno, ajuste `TARGET_DB_PUBLISHED_PORT`.
Por exemplo, `TARGET_DB_PUBLISHED_PORT=5434` publica o PostgreSQL como `localhost:5434`,
mas o container da aplicação continua acessando `target-db:5432`.


## Arquivos gerados

Após a execução, os artefatos ficam em `runs/<cliente>/<YYYYmmdd-HHMMSS>/`:

| Arquivo | Função |
|---|---|
| `manifest.json` | Metadados da execução, caminhos, modo debug, output usado e bancos sem senha |
| `logs/migration.log` | Log da geração feita pelo Python |
| `sql/00_setup.sql` | Arquivo inicial da execução |
| `sql/01_prepare_target.sql` | Preparação da V2, disable triggers/índices e limpeza |
| `sql/02_dump_compatible_tables.sql` | `pg_dump` das tabelas compatíveis |
| `sql/03_load_transformed_data.sql` | `INSERT`s e `UPDATE`s principais |
| `sql/04_finalize_target.sql` | `setval`, enable triggers e enable indexes |
| `aplicacoes/<YYYYmmdd-HHMMSS>-<modo>/` | Relatório de cada aplicação no destino, criado pelo `Aplicar.py` |

---

## Aplicando os SQLs no destino

O `Aplicar.py` aplica os arquivos de `sql/` de uma execução já gerada, na ordem das fases e numa única transação. Ou a carga inteira entra, ou nada entra.

```bash
export PGHOST=localhost PGPORT=5432 PGDATABASE=tenant PGUSER=tenant PGPASSWORD=tenant

# Valida: executa tudo e termina em ROLLBACK
python3 Aplicar.py runs/Acme/20261124-153000 --dry-run

# Aplica: termina em COMMIT se nenhum comando falhar
python3 Aplicar.py runs/Acme/20261124-153000
```

Dentro do container, o destino local é o `target-db` e as variáveis vão pelo `docker exec`:

```bash
docker exec -it -e PGHOST=target-db -e PGDATABASE=tenant -e PGUSER=tenant -e PGPASSWORD=tenant \
    project-hermes python3 Aplicar.py runs/Acme/20261124-153000 --dry-run
```

A conexão vem das variáveis da libpq, como no `psql`. `PGHOST` e `PGDATABASE` são obrigatórias e o script não assume valor padrão para elas. Antes de conectar, ele mostra o destino e o modo.

O fluxo recomendado é rodar o dry run, ler os erros e só depois aplicar.

### Modos

| Modo | Fim da transação | Política de erro padrão |
|---|---|---|
| `apply` (padrão) | `COMMIT` se a aplicação chegar ao fim, `ROLLBACK` se falhar | `stop` |
| `dry-run` | sempre `ROLLBACK` | `continue` |

### Políticas de erro

Com `--on-error stop`, o primeiro comando com erro desfaz tudo. O relatório aponta o arquivo, a posição do comando e a mensagem do banco.

Com `--on-error continue`, só os comandos que falharam são desfeitos. Cada erro vai para o relatório e a aplicação segue. É o que o `rodar2.sh` faz, só que agora com os erros listados. No dry run isso mostra todos os erros de uma vez. No `apply`, a carga é confirmada sem as linhas que falharam, então use com cuidado.

O `continue` agrupa os comandos em lotes de 1000 com um savepoint por lote. Quando um comando falha, o lote volta ao savepoint e é reexecutado comando a comando.

### O que o aplicador não executa

- metacomandos do `psql`, como `\restrict` e `\unrestrict`, que o `pg_dump` grava;
- `BEGIN`, `COMMIT` e os outros comandos de controle de transação, que o `pg_dump` grava em volta de large objects;
- no dry run, os `SELECT ... setval(...)`, porque o `ROLLBACK` não desfaz uma sequence.

Cada comando pulado aparece contado no relatório. Um `COPY ... FROM stdin` encerra a aplicação com erro, porque os dados viriam no formato do `psql`. O dump atual usa `--column-inserts` e não gera `COPY`.

Depois de cada arquivo o aplicador executa `RESET ALL`. Assim o `search_path` vazio que o `pg_dump` configura não chega ao `03` nem ao `04`, como acontecia quando cada arquivo rodava numa sessão de `psql`.

### Relatório

Cada aplicação cria uma pasta dentro da execução. O `manifest.json` e os arquivos de `sql/` não mudam.

```text
runs/Acme/20261124-153000/aplicacoes/
  20261124-160000-dry-run/
    relatorio.json
    erros.txt
  20261124-161500-apply/
    relatorio.json
    erros.txt
```

O `relatorio.json` traz modo, política de erro, destino sem senha, início, fim, resultado (`committed`, `rolled_back` ou `failed`) e o motivo da falha. Por arquivo, traz comandos executados, comandos com erro, comandos pulados por motivo e duração.

O `erros.txt` tem um bloco por comando que falhou, com arquivo, número do comando, linha inicial, SQLSTATE, mensagem e o comando truncado em 500 caracteres. Ele é gravado à medida que os erros acontecem, então dá para acompanhar uma carga longa:

```text
== 03_load_transformed_data.sql, comando 2, linha 2, SQLSTATE 23505
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
| 1 | a aplicação falhou, a execução estava incompleta ou o destino recusou a conexão |
| 2 | faltou `PGHOST` ou `PGDATABASE`, ou algum argumento é inválido |
| 130 | interrompida com Ctrl+C; a transação foi desfeita |

Um dry run com erros termina com código 0. Confira a linha `Comandos com erro` no fim da saída ou o `erros.txt`.

### Cuidados

- O destino fica bloqueado durante toda a aplicação, porque o `TRUNCATE` segura o lock até o fim da transação. Rode só em janela sem uso concorrente, como já acontece com a carga.
- O aplicador recusa execuções com status diferente de `success` ou sem algum arquivo de `sql/`. No modo debug o `02_dump_compatible_tables.sql` não existe e não é exigido.
- Num `apply` que falha, os `setval` que já rodaram continuam valendo, porque sequences não voltam no `ROLLBACK`. As tabelas voltam ao estado anterior e a próxima carga ajusta as sequences de novo.
- No dry run, o `nextval` dos defaults de coluna avança as sequences. A carga real corrige isso pelos `setval` do dump e do `04`.
- Um erro perto do fim descarta a carga inteira. Em cargas longas, rode o dry run antes, de preferência numa cópia do destino.

### Alternativa: `rodar2.sh`

O `rodar2.sh` continua disponível, por exemplo para um servidor sem Python. Ele aplica cada arquivo com `psql` em autocommit e não para no erro. Quando algo falha, o destino fica carregado pela metade e precisa ser recriado (ver [Resetar o banco V2 local](#resetar-o-banco-v2-local-antes-de-reexecutar)).

```bash
RUN_DIR=runs/Acme/20260910-220000 ./rodar2.sh
```

O script lê os SQLs de `$RUN_DIR/sql/`, grava logs em `$RUN_DIR/logs/` e grava erros filtrados em `$RUN_DIR/erros/`.

#### Com Docker manual

```bash
date ; cat "$RUN_DIR/sql/00_setup.sql" | psql -a -h target-db -d tenant -p 5432 -U tenant > "$RUN_DIR/logs/00_setup.sql.log" 2>&1 ; date
date ; cat "$RUN_DIR/sql/01_prepare_target.sql" | psql -a -h target-db -d tenant -p 5432 -U tenant > "$RUN_DIR/logs/01_prepare_target.sql.log" 2>&1 ; date
date ; cat "$RUN_DIR/sql/02_dump_compatible_tables.sql" | psql -a -h target-db -d tenant -p 5432 -U tenant tenant > "$RUN_DIR/logs/02_dump_compatible_tables.sql.log" 2>&1 ; date
date ; cat "$RUN_DIR/sql/03_load_transformed_data.sql" | psql -a -h target-db -d tenant -p 5432 -U tenant > "$RUN_DIR/logs/03_load_transformed_data.sql.log" 2>&1 ; date
date ; cat "$RUN_DIR/sql/04_finalize_target.sql" | psql -a -h target-db -d tenant -p 5432 -U tenant > "$RUN_DIR/logs/04_finalize_target.sql.log" 2>&1 ; date
```

#### Verificando erros nos logs

```bash
grep "ERROR:\|WARNING:" -B 5 -A 5 "$RUN_DIR/logs/04_finalize_target.sql.log" > "$RUN_DIR/erros/04_finalize_target_contexto.txt"
grep "ERROR:\|WARNING:" -B 15 -A 15 "$RUN_DIR/logs/03_load_transformed_data.sql.log" > "$RUN_DIR/erros/03_load_transformed_data_contexto.txt"
grep "ERROR:\|WARNING:" -B 5 -A 5 "$RUN_DIR/logs/02_dump_compatible_tables.sql.log" > "$RUN_DIR/erros/02_dump_compatible_tables_contexto.txt"
grep "ERROR:\|WARNING:" -B 5 -A 5 "$RUN_DIR/logs/01_prepare_target.sql.log" > "$RUN_DIR/erros/01_prepare_target_contexto.txt"
```

Os arquivos em `$RUN_DIR/erros/` ficam vazios se não houver problemas.

---

## Resetar o banco V2 local antes de reexecutar

Útil para simular o ambiente de produção a partir de um dump de homologação. Com o `rodar2.sh`, é o caminho depois de uma carga que deu erro. Com o `Aplicar.py`, uma carga que falha não deixa dados no destino. Nesse caso o reset só é preciso para refazer uma carga confirmada.

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

Para rodar o `Aplicar.py` no servidor, ele precisa de Python 3 com `psycopg2` e de uma cópia do `Aplicar.py` e da pasta `core/`. A pasta da execução pode vir de outra máquina, porque o aplicador resolve os arquivos a partir dela e não pelos caminhos do manifesto. Se o servidor não tiver Python, rode o `Aplicar.py` de uma máquina com acesso ao destino ou use o `rodar2.sh` no servidor.
