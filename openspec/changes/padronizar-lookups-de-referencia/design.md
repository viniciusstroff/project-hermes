## Context

Hoje o mapa de referência passa por três camadas:

- `BaseMigration.load_reference_map(cache_key, sql, key_normalizer=..., value_normalizer=..., skip_null_keys=...)` repassa ao contexto.
- `MigrationContext.load_reference_map()` guarda o resultado em `_reference_maps[cache_key]` e só consulta o provider na primeira chamada com aquela chave.
- `PostgresMetadataProvider.load_reference_map(sql, ...)` executa o SQL recebido na conexão com a V2, aplica `str(value).strip()` na chave por padrão, pula chaves nulas e grava `ref_map[key] = value`, de modo que a última linha vence.

O `BaseMigration` também expõe o dicionário interno como `self._reference_maps`.

O `Lookup(name, lookup_dict, v2_name=None, fallback=None, key_normalizer=None)` recebe um `dict` comum e aplica o próprio `key_normalizer`, que por padrão também é `str(value).strip()`. O `docs/estrategias.md` mostra o caso que exige repetir a regra: o mapa é montado com chave em maiúsculas e o `Lookup` precisa de `key_normalizer=lambda v: str(v).strip().upper()` para encontrar as chaves.

O `MigrationRunner.run()` faz, nesta ordem: comandos de trigger (lidos do catálogo da V2 e guardados em cache pelo provider), hook `set_indexes_commands`, fase `prepare_target`, fase `load_transformed_data` com `run_inserts()` e fase `finalize_target`. O `Migracao.py` chama `dump_tables()` antes do `run()`.

O `DirectDatabaseOutput` do item 2 de `docs/melhorias-futuras.md` ficou adiado porque a geração lê o destino durante a carga. Os comandos de trigger já são lidos antes da primeira fase. A única leitura que acontece no meio é a dos mapas de referência.

## Goals / Non-Goals

**Goals:**

- O cliente declara um mapa simples (tabela, chave, valor, filtro de igualdade) sem escrever SQL.
- Toda leitura do destino feita pelo contexto acontece antes da fase `prepare_target`.
- A mesma regra de normalização vale para a carga do mapa e para o `Lookup`.
- Nome repetido, chave ambígua e mapa não declarado viram erro com mensagem clara, em vez de resultado silencioso.

**Non-Goals:**

- Não implementar o `DirectDatabaseOutput`. Isso fica para a change `introduzir-direct-database-output`.
- Não bloquear o acesso direto por `pg_v2_conn`, que continua na fachada até os clientes privados migrarem (item 1 do roadmap).
- Não contar as chaves sem correspondência durante a geração. A contagem de valores perdidos por strategy entra na change `registrar-metricas-da-geracao`.
- Não declarar mapas lidos da origem. A origem não é escrita pela carga e não tem o conflito que motiva esta change.
- Não suportar chave composta (ver Open Questions).

## Decisions

1. Declarar num hook e carregar no runner, antes da primeira fase.

   A migração ganha o hook `declare_reference_maps()`, que devolve uma lista de `ReferenceMap`. O runner chama o hook depois de gerar os comandos de trigger e antes de `set_indexes_commands`, carrega todos os mapas, fecha as leituras do destino (decisão 6) e só então abre `prepare_target`. Para usar um mapa, o cliente chama `self.reference_map('states_by_code')`, que só lê o que já foi carregado.

   ```python
   def declare_reference_maps(self):
       return [
           ReferenceMap(
               'states_by_code',
               table='public.states',
               key='f_code',
               value='f_id',
               key_normalizer=lambda v: str(v).strip().upper(),
           ),
       ]

   def insert_people(self):
       TableMigration(
           source_sql='SELECT {fields} FROM public.people {limit}',
           target='public.people',
           fields=[
               Copy('f_id'),
               Lookup('f_state', self.reference_map('states_by_code')),
           ],
       ).run(self.context.engine('pg_v1'))
   ```

   O conteúdo dos mapas é o mesmo de hoje. A geração não aplica nada no destino, então um mapa carregado no meio do `run_inserts()` já via o destino como ele estava antes da carga. Carregar antes da primeira fase devolve os mesmos dados.

   Alternativa considerada: a API sugerida no roadmap, `self.reference_map(name=..., table=..., key=..., value=...)`, que declara e carrega na mesma chamada. Rejeitada porque nada impediria chamá-la dentro do `run_inserts()`. O problema voltaria. Com o hook, o momento da leitura fica com o runner. O nome `reference_map()` continua, só para leitura.

   Custo: um mapa declarado é carregado mesmo que a execução não chegue a usá-lo. Mapas de referência vêm de tabelas de domínio pequenas, então o custo é aceito.

2. Declaração `ReferenceMap` em `core/references.py`.

   É um dataclass imutável:

   | Campo | Uso |
   |---|---|
   | `name` | nome único do mapa na execução |
   | `table`, `key`, `value` | tabela do destino e colunas da chave e do valor |
   | `filters` | dicionário de igualdades, como `{'f_active': True}` |
   | `query` | SQL do destino que devolve chave e valor, nessa ordem |
   | `key_normalizer`, `value_normalizer` | funções aplicadas a cada linha |
   | `skip_null_keys` | padrão `True`, como hoje |

   A declaração exige `table` ou `query`, nunca os dois. Com `table`, `key` e `value` são obrigatórios. `filters` só aceita igualdade, porque é o que dá para renderizar em qualquer dialeto. Para `IN`, join ou expressão, o cliente usa `query`, que fica documentado como recurso dependente de dialeto, no mesmo espírito da `SourceSqlExpression` da change `revisar-estrategias-dependentes-de-dialeto`.

   O valor é uma coluna só. Para ler duas colunas da mesma tabela, o cliente declara dois mapas.

3. O provider lê pares. O core normaliza.

   O contrato `MetadataProvider` troca `load_reference_map(sql, ...)` por `load_reference_pairs(reference) -> Iterable[tuple[Any, Any]]`. O `PostgresMetadataProvider` monta `SELECT <key>, <value> FROM <table> WHERE <coluna> = <literal> AND ...` com `quote_identifier` e `serialize_value` do `PostgresDialect`. Com `query`, executa o texto como veio.

   A montagem do mapa sai do provider e vai para uma função do core em `core/references.py`, que recebe a declaração e os pares. Ela pula chaves nulas, aplica os normalizadores e confere repetições. Assim a regra é a mesma para qualquer provider e é testada sem banco.

4. O mapa carregado é um `Mapping` que guarda o normalizador.

   `LoadedReferenceMap` implementa `collections.abc.Mapping` (`[]`, `get`, `in`, `len`, iteração), não aceita escrita e expõe `name` e `normalize_key(raw)`. O normalizador padrão continua `str(value).strip()`.

   O `Lookup` passa a verificar o tipo do dicionário. Com um `LoadedReferenceMap` e sem `key_normalizer`, usa `lookup_dict.normalize_key`. Com `key_normalizer` explícito, o explícito vale. Com `dict` comum, nada muda.

   Alternativa considerada: o `Lookup` receber só o nome do mapa e resolver na hora de renderizar. Rejeitada porque a strategy não tem acesso ao contexto na hora de gerar o valor. Além disso, o mapa já está carregado quando a `TableMigration` é montada.

5. Chave ambígua é erro.

   Se duas linhas chegam à mesma chave depois da normalização com valores diferentes, a carga falha com `ReferenceMapError` contendo o nome do mapa, a chave e os dois valores. Com o mesmo valor, a repetição é aceita. O caso que mais preocupa é a normalização juntar chaves que no banco eram diferentes, como `sp` e `SP `, apontando para ids diferentes. Hoje isso passa calado e o resultado muda de uma execução para outra.

   Alternativa considerada: uma opção `on_duplicate='error' | 'first' | 'last'`. Fica de fora até aparecer um cliente que precise. Um filtro na declaração ou um `query` com `DISTINCT ON` resolve repetições legítimas.

6. Fechar as leituras do destino depois da carga.

   O contrato `MetadataProvider` ganha `close_reads()`. Depois dessa chamada, todo método do provider que consultaria o destino levanta `TargetReadClosedError`. Os comandos de trigger já guardados em cache continuam disponíveis, porque não consultam o banco de novo. O runner chama `close_reads()` logo depois de carregar os mapas.

   O `close_reads()` também encerra com `ROLLBACK` a transação que o `psycopg2` abriu na primeira leitura. Hoje essa transação fica aberta até o fim da execução, segurando `ACCESS SHARE` nas tabelas lidas e no catálogo. Sem escrita no destino durante a geração isso não atrapalha, mas um `TRUNCATE` vindo de outra conexão ficaria esperando por ela.

   O contexto ganha `reference_map(name)`, que devolve o mapa carregado ou levanta `ReferenceMapError` dizendo que o mapa não foi declarado em `declare_reference_maps()`.

   Essa é a garantia de que o `DirectDatabaseOutput` precisa: depois que a primeira fase abre, o core não lê mais o destino. Ela não cobre código do cliente que usa `pg_v2_conn` direto. Essa limitação fica documentada.

7. Logar o que foi carregado.

   Para cada mapa, o runner registra no log o nome e a quantidade de chaves. Um mapa vazio gera um aviso no log, porque quase sempre indica tabela, filtro ou banco errado. Não é erro: um destino novo pode ter tabela de domínio vazia de propósito.

   Se a declaração for inválida, se dois mapas tiverem o mesmo nome ou se a carga falhar, a execução termina com manifesto `failed`, a mensagem cita o mapa e nenhuma fase além de `setup` é aberta.

8. Remover a API antiga sem alias.

   O projeto não está em produção. Saem `MigrationContext.load_reference_map()`, `BaseMigration.load_reference_map()`, `MetadataProvider.load_reference_map()`, o cache `_reference_maps` indexado por texto livre e o atributo `BaseMigration._reference_maps`. Manter os dois caminhos deixaria aberta a leitura no meio da geração, que é o que esta change fecha.

## Risks / Trade-offs

- Clientes privados que usam `load_reference_map()` quebram na próxima execução. Mitigação: procurar `load_reference_map` e `_reference_maps` nos repositórios privados antes da próxima carga e migrar cada chamada para uma declaração.
- Um cliente pode montar mapa por `pg_v2_conn` no meio da geração e contornar a garantia. Mitigação: documentar o limite e remover `pg_v2_conn` junto com os outros atributos legados quando o item 1 for concluído.
- A regra de chave ambígua pode quebrar um cliente que hoje depende do último valor vencer. Mitigação: a mensagem de erro mostra a chave e os valores. Um filtro na declaração resolve. Se algum cliente precisar de `first`, a opção entra depois.
- Mapas grandes ficam na memória a execução toda. Já é assim hoje, porque o cache do contexto nunca é limpo.

## Migration Plan

1. Criar `core/references.py` com a declaração, o mapa carregado e a montagem, com testes.
2. Trocar o contrato do provider e implementar no `PostgresMetadataProvider`, incluindo `close_reads()`.
3. Carregar os mapas no runner e expor `reference_map(name)` no contexto e na fachada.
4. Ajustar o `Lookup`.
5. Remover a API antiga e ajustar os testes que a usavam.
6. Mostrar a declaração no `Acme` e atualizar a documentação.
7. Se o `AcmeEdu` já existir, migrar o mapa de `student_statuses` e comparar o SQL gerado antes e depois da change.

Rollback: reverter os commits. Nenhum arquivo gerado muda de formato.

## Open Questions

- Algum cliente privado precisa de chave composta, como tipo e código? Hoje dá para concatenar no `query`. Se aparecer em mais de um cliente, a declaração pode aceitar `key=('f_type', 'f_code')` com chave em tupla.
- Algum cliente privado tem chaves repetidas legítimas numa tabela de referência do destino?
- Remover `pg_v2_conn` da fachada nesta change ou esperar o item 1? Remover agora fecha a garantia por completo, mas obriga a migrar de uma vez os clientes que leem o destino direto.
