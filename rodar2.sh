 
date ; cat sql/cmd_ini.sql | psql -a -h localhost -d judice-tenant -p 4003 -U judice-tenant judice-tenant > logs/cmd_ini.sql.log 2>&1 ; date
date ; cat sql/webstagepgj_cmd.dmp.sql | psql -a -h localhost -d judice-tenant -p 4003 -U judice-tenant judice-tenant > logs/webstagepgj_cmd.dmp.sql.log 2>&1 ; date
date ; cat sql/cmd.sql | psql -a -h localhost -d judice-tenant -p 4003 -U judice-tenant judice-tenant > logs/cmd.sql.log 2>&1 ; date
date ; cat sql/cmd_fim.sql | psql -a -h localhost -d judice-tenant -p 4003 -U judice-tenant judice-tenant > logs/cmd_fim.sql.log 2>&1 ; date




rodar antes roda isso antes de executar as importações da v1..
truncate users cascade;
truncate system_profiles_users cascade;
truncate clients cascade;
truncate participants cascade;
truncate publications cascade;
truncate request_scheduling cascade;
truncate rescheduling_histories cascade;
truncate extranet.users_logs cascade;
truncate groupclients cascade;
truncate controlreportareafilter cascade;
truncate closereasonsclient cascade;
truncate consultationandadvice cascade;
truncate rescheduling_appointmenttypes cascade;
truncate annotations cascade;
truncate processlinked cascade;
truncate extranet.photo_albums cascade;
truncate dbparts_names cascade;
truncate extranet.forms cascade;
truncate extranet.tidings cascade;
truncate caldav_event cascade;
truncate info cascade;



cat logs/cmd.sql.log | grep "ERROR:" | sort | uniq -u > erros/cmd_erros.txt;
cat logs/cmd_fim.sql.log | grep "ERROR:" | sort | uniq -u > erros/cmd_fim_erros.txt;
cat logs/webstagepgj_cmd.dmp.sql.log | grep "ERROR:" | sort | uniq -u > erros/webstagepgj_cmd_erros.txt;
cat logs/cmd_ini.sql.log | grep "ERROR:" | sort | uniq -u > erros/cmd_ini_erros.txt;




grep "ERROR:\|WARNING:" -B 15 -A 15 logs/cmd.sql.log > erros/cmd_erros.txt;
grep "ERROR:\|WARNING:" -B 5 -A 5 logs/cmd_fim.sql.log > erros/cmd_fim_erros.txt
grep "ERROR:\|WARNING:" -B 5 -A 5 logs/webstagepgj_cmd.dmp.sql.log > erros/webstagepgj_cmd_erros.txt
grep "ERROR:\|WARNING:" -B 5 -A 5 logs/cmd_ini.sql.log > erros/cmd_ini_erros.txt

grep "1912740" -B 5 -A 5 logs/cmd.sql > erros/teste.txt
dropdb -h localhost -p 4003 -U judice-tenant judice-tenant;
createdb -h localhost -p 4003 -U judice-tenant judice-tenant;
pg_restore -h localhost -p 4003 -U judice-tenant -W -F t -d judice-tenant ../acme_database.tar;

grep "ERROR:" -B 5 -A 5 backup/cmd.sql.log > backup/cmd_erros.txt;
grep "ERROR:" -B 5 -A 5 backup/cmd_fim.sql.log > backup/cmd_fim_erros.txt
grep "ERROR:" -B 5 -A 5 backup/webstagepgj_cmd.dmp.sql.log > backup/webstagepgj_cmd_erros.txt
grep "ERROR:" -B 5 -A 5 backup/cmd_ini.sql.log > backup/cmd_ini_erros.txt





split --lines=5000 -d logs/webstagepgj_cmd.dmp.sql.log cmd
grep "2191454" -B 5 -A 5 sql/cmd.sql > erros/cmd_erros.txt;
