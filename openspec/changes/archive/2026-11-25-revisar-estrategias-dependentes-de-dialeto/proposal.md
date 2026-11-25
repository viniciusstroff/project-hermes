## Why

O core já separa a serialização de valores no `TargetAdapter` e no `Dialect`, mas as strategies de campo ainda carregam detalhes do PostgreSQL em quatro pontos:

- `FieldStrategy.value()` e a função `sql_value()` de `core/strategies.py` serializam com um `PostgresDialect` fixo, sem passar pelo adapter de destino. O `TableMigration` e o `MultiTargetMigration` aceitam strategies que só têm `value()`.
- O `RegexClean` monta `E'...'` à mão no `render()`, sem passar pelo adapter. Ele também remove toda barra invertida do resultado, mesmo quando o padrão da strategy mantém a barra. O `CpfClean` e o `PhoneClean` herdam esse comportamento.
- O `SqlExpression` injeta SQL no `SELECT` da origem sem dizer para qual banco ele foi escrito. A mesma `TableMigration` pode rodar com `pg_v1` ou `fb_v1`. Um exemplo do próprio `docs/estrategias.md` (`substring(CAST(f_name AS text) FROM 1 FOR 80)`) não roda no Firebird, que não tem o tipo `text`. O erro só aparece quando a consulta chega na origem.
- O `DateConvert` devolve a data já formatada como texto pelo `to_fmt`. O formato que o destino aceita para uma coluna de data é assunto do dialeto, não da strategy.

O item 8 de `docs/melhorias-futuras.md` pede que o caminho principal seja Python e agnóstico, com o SQL específico de banco explícito como recurso avançado.

## What Changes

- As strategies devolvem só valores Python, pelo `raw_value(row)`. Quem gera SQL é o `TargetAdapter`, chamado pela migration.
- **BREAKING:** saem `FieldStrategy.value()`, `FieldStrategy.render()` e `core.strategies.sql_value()`. O `TableMigration` e o `MultiTargetMigration` passam a exigir `raw_value()`. SQL explícito no destino continua possível com `SqlLiteral`. O `BaseMigration.sql_value()` continua, porque já usa o adapter do contexto.
- O `RegexClean` deixa de montar o literal e deixa de remover a barra invertida. Ganha `v2_name`, como o `CpfClean` e o `PhoneClean`.
- **BREAKING:** o `SqlExpression` vira `SourceSqlExpression(expression, col, dialect=...)`, com o dialeto da origem obrigatório. As migrations conferem o dialeto do adapter de origem antes de executar a consulta e falham com mensagem clara quando ele não bate.
- Os dialetos ganham um `name` (`postgres`, `firebird`) e o contrato `SourceAdapter` passa a expor o `dialect`.
- Novas strategies portáveis, feitas em Python: `NullIfBlank`, `Truncate`, `Upper`, `Lower` e `Default`. Elas cobrem os usos de `SqlExpression` mostrados na documentação.
- **BREAKING:** o `DateConvert` devolve `datetime.date`, ou `datetime.datetime` com `as_datetime=True`. O parâmetro `to_fmt` sai.
- `docs/estrategias.md` separa strategies portáveis das dependentes de dialeto.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `adapter-agnostic-migration-core`: as strategies deixam de gerar SQL, o SQL de origem declara o dialeto e o core ganha strategies portáveis.

## Impact

- Código afetado: `core/strategies.py`, `core/table_migration.py`, `core/multi_target_migration.py`, `core/dialects/`, `core/adapters/source_adapter.py` e os testes que usam `.value()` e `sql_value()`.
- Depende da change `preservar-barra-invertida-nos-literais`. Sem a barra dobrada pelo `PostgresDialect`, um `RegexClean` que mantém a barra voltaria a gerar escape no destino.
- Clientes privados que usam `SqlExpression`, `to_fmt`, `.value()`, `sql_value()` ou strategies próprias só com `value()` precisam de ajuste antes da próxima carga.
- No SQL gerado para o PostgreSQL, as colunas de `RegexClean`, `CpfClean` e `PhoneClean` trocam `E'123'` por `'123'`. O valor gravado no destino é o mesmo.
- A change `serializar-tipos-especiais-no-dialeto` trata os tipos que o `PostgresDialect` ainda serializa mal (`bytes`, `NaN`, caractere NUL). As duas mexem em arquivos vizinhos e podem entrar em qualquer ordem.
