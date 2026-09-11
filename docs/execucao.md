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
# Migração completa dentro do container
docker exec -it migracao python3 Migracao.py Acme

# Debug
docker exec -it migracao python3 Migracao.py Acme --debug 100
```


## Arquivos gerados

Após a execução, os arquivos ficam em `<Acme>/sql/`:

| Arquivo | Executar quando |
|---|---|
| `empty.sql` | Antes de tudo (inicialização) |
| `webstagepgj_cmd.dmp.sql` | Segundo (pg_dump das tabelas compatíveis) |
| `cmd_ini.sql` | Terceiro (disable triggers, DELETEs) |
| `cmd.sql` | Quarto (INSERTs e UPDATEs principais) |
| `cmd_fim.sql` | Por último (setval, enable triggers/indexes) |

---

## Executando os SQLs no banco V2


### Com Docker

```bash
date ; cat sql/empty.sql | psql -a -h tenant-database -d tenant -p 5432 -U tenant > logs/empty.sql.log 2>&1 ; date
date ; cat sql/cmd_ini.sql | psql -a -h tenant-database -d tenant -p 5432 -U tenant > logs/cmd_ini.sql.log 2>&1 ; date
date ; cat sql/webstagepgj_cmd.dmp.sql | psql -a -h tenant-database -d tenant -p 5432 -U tenant tenant > logs/webstagepgj_cmd.dmp.sql.log 2>&1 ; date
date ; cat sql/cmd.sql | psql -a -h tenant-database -d tenant -p 5432 -U tenant > logs/cmd.sql.log 2>&1 ; date
date ; cat sql/cmd_fim.sql | psql -a -h tenant-database -d tenant -p 5432 -U tenant > logs/cmd_fim.sql.log 2>&1 ; date
```

---

## Verificando erros nos logs

```bash
grep "ERROR:\|WARNING:" -B 5 -A 5 logs/cmd_fim.sql.log > erros/cmd_fim_erros.txt
grep "ERROR:\|WARNING:" -B 15 -A 15 logs/cmd.sql.log > erros/cmd_erros.txt
grep "ERROR:\|WARNING:" -B 5 -A 5 logs/webstagepgj_cmd.dmp.sql.log > erros/webstagepgj_cmd_erros.txt
grep "ERROR:\|WARNING:" -B 5 -A 5 logs/cmd_ini.sql.log > erros/cmd_ini_erros.txt
```

Os arquivos em `erros/` ficam vazios se não houver problemas.

---

## Resetar o banco V2 local antes de reexecutar

Útil para simular o ambiente de produção a partir de um dump de homologação:

```bash
dropdb -h localhost -p 4003 -U judice-tenant judice-tenant
createdb -h localhost -p 4003 -U judice-tenant judice-tenant
pg_restore -h localhost -p 4003 -U judice-tenant -W -F t -d judice-tenant --no-owner --no-privileges ../acme_database.tar
```


## Debug com breakpoint (VS Code + Docker)

```bash
docker exec -it migracao python3 -m debugpy --listen 0.0.0.0:5678 --wait-for-client Migracao.py Acme
```

Em seguida pressione `F5` no VS Code (configuração `launch.json` já existente no projeto).

---

## Transferindo arquivos para o servidor

```bash
# Enviando as queries geradas (compactadas)
scp sql/Acme.zip seu_usuario@servidor:

# Obtendo os logs de volta
scp seu_usuario@dev.officeadv.com.br:/tmp/Acme_logs.zip .
```
