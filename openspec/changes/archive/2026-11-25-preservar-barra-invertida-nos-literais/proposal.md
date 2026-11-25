## Why

O `PostgresDialect.serialize_value` gera `E'...'` quando o valor tem barra invertida, mas não dobra a barra. Numa string `E'...'` o PostgreSQL trata a barra como escape, então o texto que chega ao destino não é o que saiu da V1. `a\b` vira `a` seguido de backspace, `\x41\101` vira `AA` e `C:\novo` ganha uma quebra de linha. Um valor que termina em barra deixa a aspa final escapada: sozinho dá erro de sintaxe e, dentro do arquivo, engole o comando seguinte.

O defeito está no código desde o primeiro commit e atinge toda tabela carregada pelo `03_load_transformed_data.sql`. As tabelas do `02`, gravadas pelo `pg_dump`, preservam a barra, então o mesmo dado chega ao destino diferente conforme o caminho.

## What Changes

- `PostgresDialect.serialize_value` passa a dobrar a barra invertida dentro de `E'...'`. Strings sem barra continuam em `'...'` e o literal gerado para elas não muda.
- O teste que fixava a saída errada (`sql_value(r'a\b') == r"E'a\b'"`) passa a esperar `E'a\\b'`.
- Novos testes para barra no fim do valor, barra junto de aspa e textos que o PostgreSQL interpretaria como escape (`\n`, `\x41`, `\101`).
- Verificação contra PostgreSQL real de que o valor lido do destino é igual ao original, com `standard_conforming_strings` ligado e desligado.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `adapter-agnostic-migration-core`: ganha o requisito de que o dialeto PostgreSQL entrega ao destino o texto sem alteração, inclusive com barra invertida, qualquer que seja o `standard_conforming_strings` da sessão.

## Impact

- Código: `core/dialects/postgres.py`. O `PostgresTargetAdapter.serialize_value`, o `sql_value()` de `core/strategies.py` e o `BaseMigration.sql_value()` usam o dialeto e recebem a correção sem mudança própria.
- Arquivos gerados: muda só o literal de valores com barra invertida, de `E'a\b'` para `E'a\\b'`.
- Cargas já feitas: valores com barra invertida carregados pelo `03` podem estar alterados no destino. Esta change não corrige esses dados (Open Questions do design).
- Scripts de cliente que passam texto com `\n` ao `sql_value()` esperando uma quebra de linha mudam de resultado. Os clientes privados não estão neste repositório.
