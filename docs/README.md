# Documentação da Migração Acme

Esta pasta descreve a arquitetura do script `Acme/Migracao.py`, o fluxo operacional da migração e o checklist específico do Acme Acme.

Arquivos:

- `arquitetura.md`: componentes, conexões e artefatos gerados.
- `fluxo-migracao.md`: sequência de execução do script e papel de cada arquivo SQL.
- `operacao-Acme.md`: passo a passo prático para rodar a migração do Acme Acme hoje.

Ponto de entrada:

- Script principal: [Migracao.py](/home/office/projects/Migracao/Acme/Migracao.py)
- Configuração local: [Acme/.env](/home/office/projects/Migracao/Acme/.env)

Observação importante:

- O script usa `enable_debug(100)` no `main`, então por padrão ele gera queries limitadas a 100 registros por seleção. Para uma migração completa, isso precisa ser removido, comentado, ou substituído por uma execução sem `LIMIT`.
