## Context

O core atual já separa parte da declaração da migração (`TableMigration`, `ExpandMigration`, `MultiTargetMigration`) da execução, mas ainda instancia e consome drivers concretos diretamente. `BaseMigration` abre conexões `psycopg2` e `firebirdsql`; `MigrationEngine` conhece cursores PostgreSQL e Firebird; `TableMigration` e `MultiTargetMigration` montam SQL de destino diretamente; `strategies.py` serializa valores com regras PostgreSQL; e operações de triggers, índices, sequences e lookups dependem do PostgreSQL V2.

O objetivo desta mudança é tornar esses detalhes intercambiáveis sem alterar a experiência principal dos Acmes existentes. O fluxo padrão continua lendo PostgreSQL V1 e Firebird V1, gerando SQL PostgreSQL V2 em arquivos de artefato.

## Goals / Non-Goals

**Goals:**

- Definir contratos explícitos para `SourceAdapter`, `TargetAdapter`, `Dialect` e `MetadataProvider`.
- Fazer `MigrationEngine` depender de `SourceAdapter` para cursor, scalar e cláusula de limite.
- Fazer a geração de `INSERT` e serialização de valores depender do destino/dialeto, não de helpers PostgreSQL espalhados.
- Concentrar triggers, índices, sequences e lookups de referência em providers de metadados.
- Manter adapters concretos para PostgreSQL source, Firebird source e PostgreSQL target com comportamento equivalente ao atual.
- Preservar compatibilidade com migrações existentes durante a transição, oferecendo aliases ou helpers para casos ainda não migrados.

**Non-Goals:**

- Não implementar suporte completo a novos bancos além dos adapters atuais.
- Não mudar o formato principal de saída; arquivos SQL continuam sendo o padrão.
- Não resolver execução direta no banco, output híbrido ou manifesto expandido.
- Não reescrever todos os Acmes privados; esta mudança define o core e migra o Acme modelo como referência.

## Decisions

1. Introduzir adapters como contratos pequenos e composáveis.

   `SourceAdapter` expõe `cursor()`, `scalar(sql)` e `limit_clause(limit)`. `TargetAdapter` expõe `insert_statement(table, columns, values)` e carrega um `Dialect`. `MetadataProvider` cobre operações auxiliares do destino, como mapas de referência, triggers, índices e sequences.

   Alternativa considerada: criar uma classe única `DatabaseAdapter` para tudo. Rejeitada porque origem e destino têm responsabilidades diferentes; origem lê linhas, destino serializa operações e metadados.

2. Separar dialeto de conexão.

   O `Dialect` deve responder por serialização de valores, quoting de identificadores e diferenças sintáticas como `LIMIT`/`ROWS`. O adapter usa o dialeto, mas o core não deve chamar funções PostgreSQL diretamente.

   Alternativa considerada: colocar todas as regras no `TargetAdapter`. Rejeitada porque algumas regras também são úteis para adapters de origem e para estratégias que expressam SQL de leitura.

3. Manter `MigrationEngine` como orquestrador leve.

   O engine passa a receber `source_adapter`, `target_adapter`, `writer` e callbacks de progresso. Ele não deve conhecer `psycopg2`, `firebirdsql` ou factories de cursor.

   Alternativa considerada: remover `MigrationEngine` e passar adapters diretamente para cada migration. Rejeitada porque o engine já concentra progresso e execução compartilhada, e continua sendo uma fronteira útil.

4. Migrar estratégias de campo para valores estruturados de forma compatível.

   As estratégias devem passar a produzir valores Python ou expressões intencionais que o `TargetAdapter` serializa. Para reduzir ruptura, uma camada de compatibilidade pode manter `value(row)` enquanto novas APIs como `raw_value(row)` ou `render(row, target_adapter)` são introduzidas.

   Alternativa considerada: exigir que todo `FieldStrategy.value()` receba o dialect imediatamente. Rejeitada porque causaria uma quebra ampla em Acmes existentes e em estratégias customizadas.

5. Encapsular metadados PostgreSQL em `PostgresMetadataProvider`.

   `set_trigger_commands()`, reset de sequences, geração de comandos de índices e `load_reference_map()` devem delegar a providers. O provider inicial preserva os comandos PostgreSQL atuais.

   Alternativa considerada: deixar triggers e sequences como métodos sobrescritos por Acme. Rejeitada porque perpetua SQL PostgreSQL no código de negócio e não cria a fronteira necessária para novos destinos.

6. Usar configuração existente para construir adapters padrão.

   `BaseMigration` continua lendo `.env`, mas delega a construção das conexões/adapters a factories internas. As chaves atuais (`PG_V1_*`, `PG_V2_*`, `FB_V1_*`) continuam suportadas; nomes genéricos de destino podem ser adicionados em etapa posterior sem quebrar compatibilidade.

   Alternativa considerada: trocar imediatamente todas as variáveis para `SOURCE_*` e `TARGET_*`. Rejeitada porque isso mistura refatoração arquitetural com migração operacional de ambiente.

## Risks / Trade-offs

- Adapter parcial deixar SQL PostgreSQL residual nas estratégias -> mitigar com testes focados em serialização de `INSERT` e busca por usos de `sql_value`, `pg_v1_conn`, `pg_v2_conn` e `fb_v1_conn`.
- Camada de compatibilidade prolongar APIs antigas -> mitigar documentando APIs antigas como transicionais e migrando o Acme modelo no mesmo change.
- Diferenças de cursor entre drivers causarem regressões de memória/performance -> mitigar preservando cursor nomeado/itersize no adapter PostgreSQL e adaptação de linhas no adapter Firebird.
- Metadados específicos de PostgreSQL ficarem grandes demais -> mitigar mantendo `PostgresMetadataProvider` separado de `PostgresTargetAdapter`.
- Mudança parecer suporte universal a bancos -> mitigar mantendo o escopo em contratos e adapters atuais, sem prometer novos destinos nesta entrega.

## Migration Plan

1. Adicionar contratos base e implementações PostgreSQL/Firebird equivalentes ao comportamento atual.
2. Atualizar `MigrationEngine` para consumir `SourceAdapter` e `TargetAdapter`.
3. Atualizar `TableMigration`, `ExpandMigration` e `MultiTargetMigration` para pedir ao destino a construção do `INSERT`.
4. Mover serialização de valores para `Dialect`/`TargetAdapter`, preservando compatibilidade temporária com estratégias existentes.
5. Mover lookups, triggers, índices e sequences para `MetadataProvider`.
6. Atualizar `BaseMigration` para construir adapters padrão a partir da configuração atual.
7. Migrar o Acme modelo para usar helpers/adapters do core em vez de conexões concretas.
8. Adicionar testes unitários de adapters, dialeto, engine e geração de SQL para garantir equivalência do fluxo atual.

Rollback: como a saída e as variáveis atuais permanecem compatíveis, o rollback é reverter a mudança do core e manter os scripts de Acme existentes. Não há migração de dados persistente associada a esta proposta.

## Open Questions

- As variáveis genéricas `TARGET_DB_*` devem entrar neste change ou em uma mudança operacional separada?
- Estratégias customizadas em Acmes privados dependem diretamente de `sql_value()`? Se sim, a camada de compatibilidade precisa durar até esses Acmes serem revisados.
- `SqlExpression` deve permanecer como string SQL de origem ou virar uma expressão tipada por dialeto em uma etapa posterior?
