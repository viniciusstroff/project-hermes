# Como o Script Funciona

## Sequência real de execução

No `main()` do `Migracao.py`, o script faz:

1. instancia `Migracao()`;
2. aplica `enable_debug(<quantidade>)` apenas quando `--debug` é informado;
3. executa `run()`, que gera todas as fases, inclusive a cópia das entidades compatíveis;
4. com `--apply` ou `--dry-run`, fecha os arquivos gerados e aplica a execução no destino das chaves `TARGET_*`.

Isso significa que o fluxo padrão já gera:

1. um dump parcial vindo da V1;
2. os SQLs auxiliares para preparar a V2;
3. o SQL principal com inserts e updates;
4. o SQL final com ajuste de sequence e reativação de triggers.

## Etapa 1: inicialização

Durante `__init__()` o script:

- carrega `Acme/.env`;
- cria `runs/Acme/<timestamp>/`;
- inicializa `manifest.json`, `logs/migration.log` e `logs/migration.jsonl`;
- abre cada origem declarada no `.env` (`SOURCE_<NOME>_*`) e o destino (`TARGET_*`), pelo adapter de cada uma;
- abre `comandos/00_setup.sql`, com a extensão do adapter de destino;
- inicializa mapas e buffers de comandos.

O mapa `areas_map` é um exemplo de regra de negócio embutida para compatibilizar áreas entre V1 e V2.

## Etapa 2: cópia das entidades compatíveis

Depois da preparação, o runner abre a fase `copy_compatible_entities` e copia
cada `CopyEntity` devolvido por `compatible_entities()`. Só entra o que está na
lista. Cada entidade é lida pela origem que ela nomeia e cada linha vira um
`Insert`, com o mesmo nome ou com o nome de `target`.

Quando a origem tem `SOURCE_<NOME>_NATIVE_COPY=pg_dump` e o destino é
PostgreSQL, as entidades que não mudam de nome vão pelo `pg_dump`
(`-a -b --column-inserts --disable-triggers`, um `-t` por tabela). A saída
dele entra no fim do artefato da fase. No modo debug a cópia é sempre linha a
linha, para respeitar o limite.

Saída:

- `runs/<cliente>/<timestamp>/comandos/02_copy_compatible_entities.sql`

## Etapa 3: preparação da base V2

`run()` começa carregando os mapas de referência declarados e fecha a leitura do destino. Nesse momento o adapter PostgreSQL guarda a lista de tabelas que a suspensão de triggers vai usar.

Depois `run_initial()` emite as operações de `comandos/01_prepare_target.sql`:

- `clear([...])`, que vira um `TRUNCATE ... RESTART IDENTITY`;
- `suspend_constraints()`, que vira um `ALTER TABLE ... DISABLE TRIGGER ALL` por tabela;
- comandos próprios do cliente, como `Native('postgres', sql)`.

Objetivo:

- deixar a V2 em um estado carregável, reduzindo conflitos de FK, trigger e unicidade.

## Etapa 4: carga principal

Depois o script troca o arquivo ativo para `comandos/03_load_transformed_data.sql` e executa as rotinas `insert_*()` e `update_*()`.

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
- clientes fictícios e participantes;
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

No final do `run()`, o script abre `comandos/04_finalize_target.sql` e gera:

- o ajuste das sequences, pelas operações `reset_identity()` do cliente;
- a reativação das triggers de cada `suspend_constraints()` emitido antes, feita pelo runner.

Objetivo:

- devolver o banco V2 a um estado operacional depois da carga.

## O que o script não faz

- não cria banco;
- não sobe containers;
- não executa os `.sql` gerados, a não ser com `--apply` ou `--dry-run`;
- não valida o resultado funcional da aplicação.

A aplicação dos SQLs é um passo separado, pelo `Aplicar.py` ou pelas opções `--apply` e `--dry-run`. Ela roda numa única transação: se um comando falha, o `ROLLBACK` desfaz a carga inteira. Os detalhes estão em [execucao.md](execucao.md#aplicando-os-sqls-no-destino).
