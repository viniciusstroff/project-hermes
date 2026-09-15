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
```

### Via Docker

```bash
# App + PostgreSQL local de destino
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d

# Migração completa dentro do container
docker exec -it project-hermes python3 Migracao.py Acme

# Debug
docker exec -it project-hermes python3 Migracao.py Acme --debug 100
```

O contrato Docker do destino usa o hostname interno `target-db` na rede `hermes-net`.
Para alterar a porta exposta no host sem mudar o contrato interno, ajuste `TARGET_DB_PUBLISHED_PORT`.
Por exemplo, `TARGET_DB_PUBLISHED_PORT=5434` publica o PostgreSQL como `localhost:5434`,
mas o container da aplicação continua acessando `target-db:5432`.


## Arquivos gerados

Após a execução, os artefatos ficam em `runs/<cliente>/<YYYYmmdd-HHMMSS>/`:

| Arquivo | Função |
|---|---|
| `manifest.json` | Metadados da execução, caminhos, modo debug e bancos sem senha |
| `logs/migration.log` | Log da geração feita pelo Python |
| `sql/00_setup.sql` | Arquivo inicial da execução |
| `sql/01_prepare_target.sql` | Preparação da V2, disable triggers/índices e limpeza |
| `sql/02_dump_compatible_tables.sql` | `pg_dump` das tabelas compatíveis |
| `sql/03_load_transformed_data.sql` | `INSERT`s e `UPDATE`s principais |
| `sql/04_finalize_target.sql` | `setval`, enable triggers e enable indexes |

---

## Executando os SQLs no banco V2

Informe o diretório da execução criada pela migração:

```bash
RUN_DIR=runs/Acme/20260910-220000 ./rodar2.sh
```

O script lê os SQLs de `$RUN_DIR/sql/`, grava logs em `$RUN_DIR/logs/` e grava erros filtrados em `$RUN_DIR/erros/`.

### Com Docker manual

```bash
date ; cat "$RUN_DIR/sql/00_setup.sql" | psql -a -h target-db -d tenant -p 5432 -U tenant > "$RUN_DIR/logs/00_setup.sql.log" 2>&1 ; date
date ; cat "$RUN_DIR/sql/01_prepare_target.sql" | psql -a -h target-db -d tenant -p 5432 -U tenant > "$RUN_DIR/logs/01_prepare_target.sql.log" 2>&1 ; date
date ; cat "$RUN_DIR/sql/02_dump_compatible_tables.sql" | psql -a -h target-db -d tenant -p 5432 -U tenant tenant > "$RUN_DIR/logs/02_dump_compatible_tables.sql.log" 2>&1 ; date
date ; cat "$RUN_DIR/sql/03_load_transformed_data.sql" | psql -a -h target-db -d tenant -p 5432 -U tenant > "$RUN_DIR/logs/03_load_transformed_data.sql.log" 2>&1 ; date
date ; cat "$RUN_DIR/sql/04_finalize_target.sql" | psql -a -h target-db -d tenant -p 5432 -U tenant > "$RUN_DIR/logs/04_finalize_target.sql.log" 2>&1 ; date
```

---

## Verificando erros nos logs

```bash
grep "ERROR:\|WARNING:" -B 5 -A 5 "$RUN_DIR/logs/04_finalize_target.sql.log" > "$RUN_DIR/erros/04_finalize_target_contexto.txt"
grep "ERROR:\|WARNING:" -B 15 -A 15 "$RUN_DIR/logs/03_load_transformed_data.sql.log" > "$RUN_DIR/erros/03_load_transformed_data_contexto.txt"
grep "ERROR:\|WARNING:" -B 5 -A 5 "$RUN_DIR/logs/02_dump_compatible_tables.sql.log" > "$RUN_DIR/erros/02_dump_compatible_tables_contexto.txt"
grep "ERROR:\|WARNING:" -B 5 -A 5 "$RUN_DIR/logs/01_prepare_target.sql.log" > "$RUN_DIR/erros/01_prepare_target_contexto.txt"
```

Os arquivos em `$RUN_DIR/erros/` ficam vazios se não houver problemas.

---

## Resetar o banco V2 local antes de reexecutar

Útil para simular o ambiente de produção a partir de um dump de homologação:

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
