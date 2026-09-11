# Documentação da Migração Acme

Esta pasta descreve a arquitetura do script `Acme/Acme.py`, o fluxo operacional da migração e o checklist de um cliente fictício.

Arquivos:

- `arquitetura.md`: componentes, conexões e artefatos gerados.
- `fluxo-migracao.md`: sequência de execução do script e papel de cada arquivo SQL.
- `execucao.md`: passo a passo prático para rodar a migração fictícia da Acme.

Ponto de entrada:

- Script principal: `Acme/Acme.py`
- Configuração local: `Acme/.env`

Observação importante:

- O modo debug só é ativado com `--debug <quantidade>`. Sem esse parâmetro, o entry point executa `pg_dump` e gera a migração completa.
