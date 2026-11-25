## Why

Os mapas de referência são montados sob demanda. O cliente chama `load_reference_map(cache_key, sql)` quando precisa do mapa, em geral dentro do `run_inserts()`. A consulta vai para o destino pela conexão do `PostgresMetadataProvider`. Esse desenho tem cinco problemas:

- O cliente escreve o SQL da consulta. O `MetadataProvider` recebe SQL pronto e não tem como adaptar a leitura a outro destino.
- O cache é indexado só pelo `cache_key`. Duas chamadas com a mesma chave e SQL diferente devolvem o primeiro mapa, sem aviso.
- A normalização da chave aparece duas vezes, no `key_normalizer` do mapa e no `key_normalizer` do `Lookup`. Se as duas divergem, o `Lookup` cai no fallback sem erro.
- Uma chave repetida com valores diferentes fica com o último valor lido. Sem `ORDER BY`, qual valor vence depende da ordem em que o banco devolveu as linhas.
- A leitura acontece no meio da geração. É isso que impede o `DirectDatabaseOutput` do item 2 de `docs/melhorias-futuras.md` de executar os comandos enquanto gera: a leitura veria a tabela já truncada ou esperaria o lock do próprio `TRUNCATE` (decisão 1 do design de `aplicar-artefatos-sql-no-destino`).

## What Changes

- Novo hook `declare_reference_maps()` na migração. Ele devolve declarações `ReferenceMap` com nome, tabela, coluna da chave, coluna do valor, filtros de igualdade e normalizadores. Para o que não cabe nisso, como um join, a declaração aceita `query=` com SQL do destino, tratado como recurso dependente de dialeto.
- O `MigrationRunner` carrega todos os mapas declarados antes de abrir a fase `prepare_target`, logo depois de gerar os comandos de trigger.
- Depois da carga, o contexto fecha as leituras do destino. Pedir um mapa que não foi declarado, ou consultar o destino pelo provider, gera erro.
- O mapa carregado é um `Mapping` somente leitura que guarda o próprio normalizador. O `Lookup` usa esse normalizador quando recebe um mapa carregado sem `key_normalizer`.
- Chave repetida com valores diferentes é erro na carga, com o nome do mapa, a chave e os dois valores. Repetição com o mesmo valor é aceita.
- O `MetadataProvider` passa a ler pares chave e valor a partir da declaração. A normalização, o descarte de chaves nulas e a checagem de repetição ficam no core, iguais para qualquer provider.
- **BREAKING:** saem `load_reference_map()` do `MigrationContext`, do `BaseMigration` e do `MetadataProvider`, além do atributo `BaseMigration._reference_maps`. O projeto não está em produção, então não fica alias.
- Atualização de `docs/estrategias.md`, `docs/novo-cliente.md`, `docs/arquitetura.md`, `docs/melhorias-futuras.md` e do `README.md`.

## Capabilities

### New Capabilities

- `reference-maps`: declaração, carga antes da primeira fase, cache e uso dos mapas de referência lidos do destino.

### Modified Capabilities

- `adapter-agnostic-migration-core`: o mapa de referência deixa de ser carregado por SQL livre a qualquer momento e passa a ser declarado e carregado pelo provider antes da primeira fase.

## Impact

- Novo módulo `core/references.py`.
- Código afetado: `core/context.py`, `core/runner.py`, `core/base_migration.py`, `core/metadata/base.py`, `core/metadata/postgres.py` e `core/strategies.py` (`Lookup`).
- Clientes privados que chamam `load_reference_map()` precisam declarar os mapas em `declare_reference_maps()`. Quem monta mapa direto por `pg_v2_conn` continua funcionando, mas fica fora da garantia de leitura antes da primeira fase.
- A change `criar-cliente-ficticio-educacional` prevê `load_reference_map` para `student_statuses` (task 3.2). Se ela entrar antes desta, o `AcmeEdu` passa a declarar o mapa aqui. Se entrar depois, já nasce com `declare_reference_maps()`.
- Os arquivos SQL gerados não mudam. Os mapas têm o mesmo conteúdo, só são carregados antes.
- Destrava a Fase 4 do item 2 de `docs/melhorias-futuras.md`, coberta pela change `introduzir-direct-database-output`.
