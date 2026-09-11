# Arquitetura da Migração Acme

## Visão geral

O script [Migracao.py](Acme/Migracao.py) é uma aplicação CLI que:

1. Lê a configuração do Acme em `Acme/.env`.
2. Abre conexão com três bancos.
3. Gera arquivos `.sql` com `DELETE`, `UPDATE`, `INSERT`, `ALTER TABLE` e `setval`.
4. Não aplica os dados diretamente na V2.
5. Delega a execução final para `psql`, usando os arquivos gerados em `Acme/sql/`.

## Bancos envolvidos

O script usa três conexões independentes:

- `PG_V1_*`: PostgreSQL da base legada V1.
- `PG_V2_*`: PostgreSQL da base destino V2.
- `FB_V1_*`: Firebird legado, usado para complementar parte das informações migradas.

Resumo do papel de cada origem:

- `PG_V1`: principal fonte dos `SELECT`s e do `pg_dump`.
- `PG_V2`: fonte de metadados do ambiente destino, especialmente para listar tabelas e gerar comandos de trigger.
- `Firebird V1`: fonte auxiliar para algumas rotinas específicas do Acme.

## Estrutura lógica do script

A classe `Migracao` concentra toda a execução.

Principais responsabilidades:

- `__init__()`: carrega `.env`, abre conexões, inicializa arquivos e mapas.
- `dump_tables()`: roda `pg_dump` da V1 com exclusão de tabelas problemáticas ou inexistentes.
- `set_trigger_commands()`: monta `ALTER TABLE ... DISABLE/ENABLE TRIGGER ALL`.
- `set_indexes_commands()`: monta comandos para desabilitar e reabilitar índices específicos.
- `create_deletes*()`: prepara limpeza da V2 antes da carga.
- `insert_*()`: gera `INSERT`s por domínio funcional.
- `update_*()`: corrige campos após a carga.
- `reset_sequences()`: reposiciona sequences no final.
- `run()`: orquestra a ordem completa.

## Artefatos gerados

Os caminhos vêm do `.env`:

- `sql/empty.sql`
- `sql/webstagepgj_cmd.dmp.sql`
- `sql/cmd_ini.sql`
- `sql/cmd.sql`
- `sql/cmd_fim.sql`

Responsabilidade de cada artefato:

- `empty.sql`: arquivo inicializado no construtor; hoje não recebe conteúdo relevante durante `run()` mas é importante existir.
- `webstagepgj_cmd.dmp.sql`: saída do `pg_dump` da V1 para tabelas que podem ser carregadas em bloco.
- `cmd_ini.sql`: preparação da V2, com desabilitação de triggers/índices e limpeza de dados.
- `cmd.sql`: carga principal e ajustes intermediários.
- `cmd_fim.sql`: `setval` de sequences e reativação de índices/triggers.

## Estratégia de carga

O processo mistura dois modelos:

- `pg_dump --column-inserts` para copiar tabelas que podem ser migradas quase como espelho.
- `INSERT INTO ... VALUES ...` gerado em Python para tabelas que exigem mapeamento, filtros, replace, defaults ou correções.

Isso existe porque parte da base V1 não é compatível diretamente com a estrutura da V2.

## Tratamento de incompatibilidades

O script já incorpora decisões de compatibilidade:

- exclusão de tabelas problemáticas no `pg_dump`;
- exclusão de tabelas inexistentes no ambiente do Acme;
- limpeza prévia de tabelas da V2 para evitar `duplicate key`;
- recriação de índice único em `clients(f_cpf, (f_cpf is not null))`;
- correções posteriores, como prefixo de `document_files.f_file` e ajustes em datas;
- atualização de usernames com encoding inválido em IDs específicos.

## Dependências operacionais

Para funcionar, o ambiente precisa ter:

- `python3`
- `psycopg2`
- `firebirdsql`
- `python-dotenv`
- `pg_dump`
- acesso de rede aos bancos configurados

Também é esperado que existam as pastas:

- `Acme/sql`
- `Acme/logs`
- `Acme/erros`

## Riscos arquiteturais atuais

- O `main` chama `enable_debug(100)`, o que limita as consultas e impede migração completa se isso não for removido.
- O script gera SQL em arquivo, mas não executa transações fim a fim dentro do Python.
- O escaping é manual em vários `INSERT`s; isso funciona para o padrão atual, mas é frágil para casos fora da curva.
- A lista de tabelas ignoradas e deletadas é estática e depende do histórico do Acme.
