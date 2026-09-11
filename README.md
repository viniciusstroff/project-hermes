# Project Hermes
 Aplicação cli para gerar as queries de migração de dados do banco v1 para a v2

## Passos

* conectar no ssh o banco da v1, por padrão 3050 => firebird, 3396 => banco da V1
  exemplo
  ```bash
  -L 172.17.0.1:3050:10.0.2.XXX:3050 -L 172.17.0.1:3396:10.0.2.XXX:5432

  ```

* criar um diretorio com o nome do Acme a ser migrado, copiando como base a ultima migração realizada(de preferência)

* criar o .env para o Acme a ser migrado

## Observações
 * na raiz do projeto o arquivo queries_duplicidade_dados.sql, são queries comuns para serem executadas no banco da V1, quando há duplicidade de registros

```
após finalizado, os arquivos com as queries a serem executadas serão geradas.

* empty.sql
* webstagepgj_cmd.dmp.sql
* cmd_fim.sql
* cmd_ini.sql
* cmd.sql

## Rodar queries de migração geradas:
```bash
# Caso não utilize o docker
date ; cat sql/empty.sql | psql -a -h localhost -d judice-tenant -p 5432 -U judice-tenant judice-tenant > logs/empty.sql.log 2>&1 ; date
date ; cat sql/cmd_ini.sql | psql -a -h localhost -d judice-tenant -p 4003 -U judice-tenant judice-tenant > logs/cmd_ini.sql.log 2>&1 ; date
date ; cat sql/webstagepgj_cmd.dmp.sql | psql -a -h localhost -d judice-tenant -p 4003 -U judice-tenant judice-tenant > logs/webstagepgj_cmd.dmp.sql.log 2>&1 ; date
date ; cat sql/cmd.sql | psql -a -h localhost -d judice-tenant -p 4003 -U judice-tenant judice-tenant > logs/cmd.sql.log 2>&1 ; date
date ; cat sql/cmd_fim.sql | psql -a -h localhost -d judice-tenant -p 4003 -U judice-tenant judice-tenant > logs/cmd_fim.sql.log 2>&1 ; date

#Para o caso de utilizar o docker @Legal
date ; cat sql/empty.sql | psql -a -h tenant-database -d tenant -p 5432 -U tenant > logs/empty.sql.log 2>&1 ; date
date ; cat sql/cmd_ini.sql | psql -a -h tenant-database -d tenant -p 5432 -U tenant > logs/cmd_ini.sql.log 2>&1 ; date
date ; cat sql/webstagepgj_cmd.dmp.sql | psql -a -h tenant-database -d tenant -p 5432 -U tenant tenant > logs/webstagepgj_cmd.dmp.sql.log 2>&1 ; date
date ; cat sql/cmd.sql | psql -a -h tenant-database -d tenant -p 5432 -U tenant > logs/cmd.sql.log 2>&1 ; date
date ; cat sql/cmd_fim.sql | psql -a -h tenant-database -d tenant -p 5432 -U tenant > logs/cmd_fim.sql.log 2>&1 ; date
```

## Verificar erros e warnings dos logs

```bash
grep "ERROR:\|WARNING:" -B 5 -A 5 logs/cmd_fim.sql.log > erros/cmd_fim_erros.txt
grep "ERROR:\|WARNING:" -B 15 -A 15 logs/cmd.sql.log > erros/cmd_erros.txt;
grep "ERROR:\|WARNING:" -B 5 -A 5 logs/webstagepgj_cmd.dmp.sql.log > erros/webstagepgj_cmd_erros.txt
grep "ERROR:\|WARNING:" -B 5 -A 5 logs/cmd_ini.sql.log > erros/cmd_ini_erros.txt
```

## Enviando as queries e Obtendo os logs
Compactar os arquivos gerados das queries para serem enviados
```bash
#enviando queries compactado
scp sql/Acme.zip seu_usuario@servidor.com.br:

```





Para debugar:

docker exec -it migracao python3 -m debugpy --listen 0.0.0.0:5678 --wait-for-client Migracao.py
