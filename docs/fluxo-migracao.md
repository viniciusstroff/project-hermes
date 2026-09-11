# Como o Script Funciona

## Sequência real de execução

No bloco `main`, o script faz:

1. instancia `Migracao()`;
2. aplica `enable_debug(100)`;
3. executa `dump_tables()`;
4. executa `run()`.

Isso significa que o fluxo padrão já gera:

1. um dump parcial vindo da V1;
2. os SQLs auxiliares para preparar a V2;
3. o SQL principal com inserts e updates;
4. o SQL final com ajuste de sequence e reativação de índices/triggers.

## Etapa 1: inicialização

Durante `__init__()` o script:

- carrega `Acme/.env`;
- abre conexão com PostgreSQL V1;
- abre conexão com PostgreSQL V2;
- abre conexão com Firebird V1;
- inicializa mapas e buffers de comandos.

O mapa `areas_map` é um exemplo de regra de negócio embutida para compatibilizar áreas entre V1 e V2.

## Etapa 2: dump das tabelas compatíveis

`dump_tables()` monta um `pg_dump` com:

- `-a -b --column-inserts`
- `--disable-triggers`
- exclusão (`-T`) de tabelas problemáticas;
- exclusão de tabelas inexistentes no Acme.

Objetivo:

- aproveitar o `pg_dump` para partes da base que não precisam de transformação linha a linha.

Saída:

- `sql/webstagepgj_cmd.dmp.sql`

## Etapa 3: preparação da base V2

`run()` começa gerando:

- comandos para desabilitar triggers;
- comandos para desabilitar índices específicos.

Depois escreve `sql/cmd_ini.sql` com:

- `ALTER TABLE ... DISABLE TRIGGER ALL`;
- updates diretos em `pg_index` para certos índices;
- `DELETE`s em tabelas de destino;
- `DELETE`s em tabelas do schema `extranet`;
- `DELETE`s em tabelas sem sequence;
- ajuste do índice único de CPF em `clients`.

Objetivo:

- deixar a V2 em um estado carregável, reduzindo conflitos de FK, trigger e unicidade.

## Etapa 4: carga principal

Depois o script troca o arquivo ativo para `sql/cmd.sql` e executa dezenas de rotinas `insert_*()` e `update_*()`.

A ordem importa. Em alto nível:

- usuários e grupos;
- permissões;
- advogados;
- processos e vínculos;
- agenda e compromissos;
- acordos;
- publicações;
- documentos;
- audiências, intimações e históricos;
- Acmes e participantes;
- áreas;
- dados financeiros e complementares;
- correções finais do conjunto principal.

## Padrão usado pelos inserts

Há dois padrões:

### 1. Inserts especializados

Métodos como `insert_users()` ou `insert_process()` costumam:

- definir campos da V1;
- mapear nomes para a V2;
- aplicar transformações;
- escrever `INSERT`s prontos no arquivo.

### 2. Inserts genéricos

`default_custom_insert()` executa um `SELECT` na V1 e para cada linha:

- monta a lista de colunas de destino;
- aplica `fields_map`;
- aplica `fields_replace`, quando necessário;
- escapa aspas simples manualmente;
- grava um `INSERT INTO ... VALUES ...`.

Esse helper reduz duplicação, mas não faz validação semântica do dado.

## Etapa 5: finalização

No final do `run()`, o script abre `sql/cmd_fim.sql` e gera:

- `setval` para sequences;
- reativação de índices;
- reativação de triggers.

Objetivo:

- devolver o banco V2 a um estado operacional depois da carga.

## O que o script não faz

- não cria banco;
- não sobe containers;
- não executa automaticamente os `.sql` gerados;
- não valida o resultado funcional da aplicação;
- não trata rollback completo da operação.

Por isso a migração real depende da execução manual dos SQLs e da análise dos logs.
