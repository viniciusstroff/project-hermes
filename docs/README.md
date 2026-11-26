# Documentação da Migração Acme

Esta pasta descreve a arquitetura do core, o fluxo operacional da migração e como escrever ou converter um cliente.

Arquivos:

- `arquitetura.md`: componentes, contratos por papel, adapters, conexões e artefatos gerados.
- `fluxo-migracao.md`: sequência de execução do script e papel de cada artefato.
- `execucao.md`: passo a passo prático para rodar e aplicar a migração.
- `novo-cliente.md`: checklist para criar a migração de um cliente.
- `estrategias.md`: estratégias de campo e consultas da origem.
- `conversao-clientes.md`: como levar um cliente do `.env` e da API antigos para o core desacoplado.
- `melhorias-futuras.md`: plano de evolução com contexto e riscos de cada item.

Ponto de entrada:

- Script de exemplo: `Acme/Acme.py`. O cliente de referência completo fica em `AcmeEdu/`
- Configuração local: `<Cliente>/.env`

Observação importante:

- O modo debug só é ativado com `--debug <quantidade>`. Sem esse parâmetro, o entry point gera a migração completa, com a cópia inteira das entidades compatíveis.
