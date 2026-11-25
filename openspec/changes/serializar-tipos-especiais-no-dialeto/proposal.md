## Why

A change `preservar-barra-invertida-nos-literais` corrigiu as strings com barra invertida no `PostgresDialect.serialize_value` e registrou, no item 8 de `docs/melhorias-futuras.md`, tipos que ainda saem errados no SQL gerado. O método trata `None`, `bool`, `int` e `float` e converte todo o resto com `str()` entre aspas. Para texto isso funciona. Para datas e `Decimal` funciona por coincidência, porque o PostgreSQL converte o literal sem tipo para o tipo da coluna. Os outros casos falham:

- `bytes` e `memoryview`, que o `firebirdsql` e o `psycopg2` devolvem para BLOB e `bytea`, viram o texto da representação Python, como `<memory at 0x...>`. O destino grava um valor sem relação com o original e nenhum erro aparece.
- `float('nan')`, `inf` e `-inf` saem sem aspas. O PostgreSQL lê `nan` como nome de coluna. O comando só falha na aplicação.
- O caractere NUL (`\x00`) não pode ser gravado em `text` no PostgreSQL. A linha falha na aplicação, longe de onde o valor foi lido.
- O `psycopg2` devolve `list` para colunas de array e `dict` para `json` e `jsonb`. O `str()` produz `['a', 'b']` e `{'a': 1}`, que o PostgreSQL recusa ou, numa coluna de texto, grava como está.

## What Changes

- O `PostgresDialect.serialize_value` passa a aceitar uma lista fechada de tipos: `None`, `bool`, `int`, `float`, `Decimal`, `str`, `date`, `datetime`, `time`, `UUID`, `bytes`, `bytearray` e `memoryview`.
- Binários viram `decode('<hex>', 'hex')`, que não depende do `standard_conforming_strings`.
- `NaN`, `inf` e `-inf` viram `'NaN'::float8`, `'Infinity'::float8` e `'-Infinity'::float8`. `Decimal` finito sai sem aspas e `Decimal` especial vira `'NaN'::numeric` ou `'Infinity'::numeric`.
- Datas e horas saem em ISO 8601 entre aspas. Para `date` e `datetime`, é o mesmo literal que o `str()` produz hoje.
- Texto com NUL e qualquer tipo fora da lista viram erro na geração. A mensagem cita a tabela de destino, a coluna e o tipo Python, sem o valor.
- A decisão sobre o NUL fica com o cliente. A documentação mostra como removê-lo com `Transform`.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `adapter-agnostic-migration-core`: o dialeto PostgreSQL passa a serializar binários, valores numéricos especiais e tipos temporais. Valores que ele não sabe representar são recusados na geração.

## Impact

- Código afetado: `core/dialects/postgres.py`, `core/table_migration.py` e `core/multi_target_migration.py` (contexto do erro), além dos testes de dialeto.
- Depende da change `preservar-barra-invertida-nos-literais`, que altera o mesmo método.
- Uma execução que hoje termina a geração com valor errado passa a falhar na geração. É proposital: o erro aparece antes da aplicação e aponta a coluna.
- No SQL gerado, mudam os literais de `Decimal` (`'85.00'` vira `85.00`) e os dos tipos que hoje saem errados. Texto, inteiros, booleanos e datas continuam iguais.
- Atualização de `docs/estrategias.md` e do item 8 de `docs/melhorias-futuras.md`.
