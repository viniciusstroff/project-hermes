## Context

Uma strategy responde duas perguntas: quais colunas entram no `SELECT` da origem (`select_columns`) e qual valor vai para a coluna do destino (`insert_col` e `raw_value(row)`). A classe base `FieldStrategy` tem dois caminhos de serialização:

- `render(row, target_adapter)`, que chama `target_adapter.serialize_value(self.raw_value(row))`;
- `value(row)`, que chama `sql_value()`, uma função de módulo presa a `_POSTGRES_DIALECT = PostgresDialect()`.

O `TableMigration._field_value()` e o `MultiTargetMigration._field_value()` usam `render()` quando existe e caem em `value()` quando não. A validação dos dois aceita qualquer objeto com `render` ou `value`. Os testes de strategy usam `value()` quase todos.

O `RegexClean` sobrescreve os dois caminhos. O `raw_value()` aplica `re.sub` e depois `.replace('\\', '')`. O `value()` monta `"E'" + texto + "'"` e o `render()` devolve esse texto como `SqlLiteral`, sem consultar o adapter. A change `preservar-barra-invertida-nos-literais` registrou que a remoção da barra é o que impede esse `E'...'` de virar escape.

O `SqlExpression(expression, col)` devolve `f'{expression} AS {col}'` em `select_columns`. A mesma `TableMigration` roda com `context.engine('pg_v1')` ou `context.engine('fb_v1')`. O `MigrationEngine` só sabe traduzir o `{limit}`. Os adapters de origem já guardam o dialeto em `self.dialect`, mas o contrato `SourceAdapter` não expõe esse atributo e os dialetos não têm nome.

O `DateConvert(name, from_fmt='%d/%m/%Y', to_fmt='%Y-%m-%d', v2_name=None)` faz `strptime` com `from_fmt` e devolve `strftime(to_fmt)`, ou `None` quando o valor é vazio ou inválido.

## Goals / Non-Goals

**Goals:**

- Nenhuma strategy do core produz SQL. O `TargetAdapter` é o único ponto de serialização de valores.
- SQL escrito para a origem fica explícito, preso a um dialeto e conferido antes da consulta.
- Os usos de `SqlExpression` mostrados na documentação ganham equivalente portável.
- Para o destino PostgreSQL, o valor gravado não muda. O texto do literal pode mudar.

**Non-Goals:**

- Não muda o `source_sql` da `TableMigration`. Ele é escrito pelo cliente para uma origem conhecida, como hoje.
- Não cria composição de strategies. Para combinar regras, o cliente usa `Transform` (ver Open Questions).
- Não trata `bytes`, `NaN` e o caractere NUL. Isso fica na change `serializar-tipos-especiais-no-dialeto`.
- Não muda a regra das strategies de domínio. `CpfClean` de texto vazio continua devolvendo texto vazio.
- Não cria um dialeto de destino Firebird. O `FirebirdDialect` continua servindo só às consultas de origem.

## Decisions

1. As strategies devolvem valores Python. Só o adapter gera SQL.

   Saem `FieldStrategy.value()`, `FieldStrategy.render()`, a função `sql_value()` e o `_POSTGRES_DIALECT` de `core/strategies.py`. O contrato de uma strategy fica com três membros: `select_columns`, `insert_col` e `raw_value(row)`. O `_field_value()` do `TableMigration` e do `MultiTargetMigration` passa a ser `engine.target_adapter.serialize_value(field.raw_value(row))`.

   A validação das duas migrations passa a exigir `select_columns`, `insert_col` e `raw_value`. Uma strategy própria de cliente que só implementa `value()` ou `render()` falha na construção da migration, com a mensagem de validação que já existe.

   Tirar também o `render()` deixa um único ponto onde o valor vira SQL. É nesse ponto que a change `registrar-metricas-da-geracao` vai ler as perdas de dado. A change `serializar-tipos-especiais-no-dialeto` vai acrescentar a tabela e a coluna aos erros de serialização.

   O `BaseMigration.sql_value()` fica. Ele usa `context.target.serialize_value()`, então já respeita o destino configurado. É o que os clientes usam para montar SQL à mão enquanto o item 5 do roadmap não termina.

   SQL no destino continua possível de forma explícita, com `Fixed('f_created_at', SqlLiteral('CURRENT_TIMESTAMP'))` ou com uma strategy cujo `raw_value()` devolve um `SqlLiteral`. O adapter devolve o `SqlLiteral` sem mexer. A documentação passa a listar esse uso entre os recursos dependentes de dialeto.

   Alternativa considerada: manter `value()` delegando a um adapter padrão. Rejeitada porque continuaria existindo uma forma de serializar que não passa pelo destino configurado. O projeto também não mantém alias de API antiga. Manter o `render()` sobrescrevível foi descartado pelo mesmo motivo: o `SqlLiteral` já cobre o caso de SQL explícito.

2. `RegexClean` sem literal próprio e sem remover a barra.

   Saem os overrides de `render()` e `value()` e o `.replace('\\', '')`. O `raw_value()` passa a ser só `re.sub(pattern, replacement, str(raw))`, com `None` preservado. O `RegexClean` ganha `v2_name`. O `CpfClean` e o `PhoneClean` passam a usar esse parâmetro em vez de guardar `_v2_name` à parte.

   Para os padrões do `CpfClean` (`[^\d]`) e do `PhoneClean` (`[^\d+]`) nada muda no valor, porque eles já descartam a barra. No arquivo gerado, `E'12345678'` vira `'12345678'`. Para um `RegexClean` de cliente cujo padrão mantém a barra, o valor passa a manter a barra também. É o comportamento correto: a strategy faz o que o padrão diz.

   Esta decisão depende da change `preservar-barra-invertida-nos-literais`. Antes dela, o `PostgresDialect` não dobra a barra dentro de `E'...'` e o valor chegaria alterado no destino.

3. `SqlExpression` vira `SourceSqlExpression` e declara o dialeto.

   ```python
   SourceSqlExpression(
       "regexp_replace(f_doc, '[^0-9]', '', 'g')",
       'f_doc',
       dialect='postgres',
   )
   ```

   O argumento `dialect` é obrigatório e a strategy o expõe como `source_dialect`. Os dialetos ganham o atributo `name` (`PostgresDialect.name = 'postgres'`, `FirebirdDialect.name = 'firebird'`) e o contrato `SourceAdapter` passa a declarar o atributo `dialect`, que os dois adapters já têm.

   O `build_source_sql()` do `TableMigration` e do `MultiTargetMigration` confere, antes de qualquer consulta, cada strategy que tem `source_dialect`. Se o adapter de origem não tem dialeto, ou se o nome não bate, a migration levanta `ValueError` com a coluna, o dialeto esperado e o encontrado. O `ExpandMigration` herda a checagem do `TableMigration`. A conferência fica antes do `COUNT(*)` de progresso, que também monta a consulta.

   Alternativa considerada: manter o nome `SqlExpression` com `dialect` opcional. Rejeitada porque o caso sem dialeto continuaria silencioso. Além disso, o nome não diz que o SQL roda na origem. Pela regra do projeto, o nome antigo sai sem alias.

4. Strategies portáveis em Python.

   | Strategy | Substitui | Regra |
   |---|---|---|
   | `NullIfBlank(name, v2_name=None)` | `NULLIF(col, '')` | `None` para `None`, texto vazio ou só com espaços |
   | `Truncate(name, length, v2_name=None)` | `substring(col FROM 1 FOR n)` | corta texto em `length` caracteres; outros tipos passam sem mudança |
   | `Upper(name, v2_name=None)` | `upper(col)` | `str.upper()` em texto; `None` continua `None` |
   | `Lower(name, v2_name=None)` | `lower(col)` | `str.lower()` em texto; `None` continua `None` |
   | `Default(name, value, v2_name=None)` | `COALESCE(col, valor)` | troca `None` por `value` |

   Todas selecionam só a coluna `name` e escrevem em `v2_name or name`, como `Rename`. O `regexp_replace` não ganha strategy nova: o `RegexClean`, depois da decisão 2, já é um `re.sub` com `replacement`.

   Há diferenças entre a regra em Python e a função SQL. A documentação vai citá-las. O `NullIfBlank` trata texto só com espaços como vazio, o `NULLIF(col, '')` não. O `str.upper()` do Python troca `ß` por `SS`. O `upper()` do PostgreSQL depende da collation. O `Truncate` conta caracteres, como o `substring` do PostgreSQL em UTF-8.

   Os exemplos de `docs/estrategias.md` passam a ser `Truncate('f_name', 80)`, `Default('f_type', 1)` e `NullIfBlank('f_cpf')`.

5. `DateConvert` devolve data, não texto.

   O `raw_value()` devolve `datetime.date`. Com `as_datetime=True`, devolve `datetime.datetime`, para formatos de origem com hora. O `to_fmt` sai. Valor vazio ou inválido continua virando `None`.

   O `PostgresDialect` já serializa `date` e `datetime` por `str()`, que produz `'2026-06-06'` e `'2026-06-06 10:30:00'`. Para o formato padrão, o literal gerado é o mesmo de hoje. Quem usava `to_fmt` para gravar texto num formato próprio passa a usar `Transform`.

6. Catálogo separado na documentação.

   O `docs/estrategias.md` passa a ter duas partes. As portáveis: `Copy`, `Rename`, `Fixed`, `Lookup`, `Transform`, `RegexClean`, `CpfClean`, `PhoneClean`, `DateConvert`, `EmailExtract` e as novas da decisão 4. As dependentes de dialeto: `SourceSqlExpression`, `Fixed` com `SqlLiteral` e o `query` da declaração de mapa de referência da change `padronizar-lookups-de-referencia`. Essa divisão é o catálogo que o item 10 do roadmap (interface gráfica) vai precisar.

## Risks / Trade-offs

- Clientes privados quebram onde usam `SqlExpression`, `to_fmt`, `.value()`, `.render()`, `sql_value()` ou strategy própria sem `raw_value()`. Mitigação: procurar esses nomes nos repositórios privados antes da próxima carga. Cada caso tem troca direta, listada na documentação.
- Um `RegexClean` de cliente que mantém a barra passa a gravar a barra no destino. Mitigação: a mesma busca, olhando os padrões usados.
- O diff do SQL gerado cresce por causa da troca `E'...'` por `'...'`. Mitigação: a verificação compara execuções antes e depois e confere que só esses literais mudaram.
- A regra em Python pode divergir da função SQL que ela substitui (decisão 4). Mitigação: documentar as diferenças conhecidas e manter a `SourceSqlExpression` para quem precisa do comportamento exato do banco.

## Migration Plan

1. Confirmar que `preservar-barra-invertida-nos-literais` está na `main`.
2. Dar nome aos dialetos e expor `dialect` no contrato de origem.
3. Remover `value()`, `render()` e `sql_value()`, serializar nas migrations e reescrever os testes que usavam `value()` e `render()`.
4. Ajustar `RegexClean` e `DateConvert` e criar `SourceSqlExpression` com a conferência de dialeto.
5. Criar as strategies portáveis.
6. Atualizar a documentação.
7. Gerar uma execução antes e outra depois da change e comparar o SQL. Se o `AcmeEdu` já existir, a comparação usa esse cliente.

Rollback: reverter os commits. O formato dos arquivos gerados não muda.

## Open Questions

- Vale ter composição de strategies, como `Chain('f_cpf', NullIfBlank, CpfClean)`? Hoje isso se faz com `Transform`. Se o padrão se repetir nos clientes privados, uma composição declarativa ajuda o catálogo da interface gráfica.
- Algum cliente privado usa `SqlExpression` com função que não tem equivalente portável? A busca da mitigação responde. Se houver um uso recorrente, ele vira strategy.
- O `Upper` e o `Lower` devem seguir a regra do PostgreSQL para `ß` e casos parecidos? Hoje a decisão é seguir o Python e documentar.
