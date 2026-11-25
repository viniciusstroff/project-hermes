## Context

O `PostgresDialect.serialize_value`, em `core/dialects/postgres.py`, escapa só a aspa simples:

```python
escaped = str(value).replace("'", "''")
if '\\' in escaped:
    return "E'" + escaped + "'"
return "'" + escaped + "'"
```

Ele é o ponto único de serialização de valores para o destino. Passam por ele os INSERTs do `TableMigration` e do `MultiTargetMigration` (via `PostgresTargetAdapter.serialize_value`), o `sql_value()` de `core/strategies.py` e o `BaseMigration.sql_value()`. O `RegexClean.value()` monta `E'...'` à mão, mas o `raw_value()` remove toda barra antes, então não é afetado.

Comparação contra um PostgreSQL 16, lendo o valor com `SELECT <literal>` e comparando com o original. O resultado foi o mesmo com `standard_conforming_strings` ligado e desligado:

| Valor original | Literal atual | Lido do banco hoje | Com a barra dobrada |
|---|---|---|---|
| `C:\novo\arquivo` | `E'C:\novo\arquivo'` | `C:` + quebra de linha + `ovoarquivo` | igual ao original |
| `a\b` | `E'a\b'` | `a` + backspace | igual |
| `\x41\101` | `E'\x41\101'` | `AA` | igual |
| `fim\` | `E'fim\'` | erro de sintaxe | igual |
| `O\'Neil` | `E'O\''Neil'` | erro de sintaxe | igual |
| `sem barra` | `'sem barra'` | igual | igual, mesmo literal |

## Goals / Non-Goals

**Goals:**

- O texto lido do destino é igual à string Python serializada, para qualquer conteúdo.
- O resultado não depende do `standard_conforming_strings` da sessão que aplica o arquivo.
- O literal de strings sem barra invertida continua o mesmo.

**Non-Goals:**

- Não muda o `RegexClean`, que remove a barra antes de gerar `E'...'`.
- Não muda o `FirebirdDialect`, usado só nas consultas à origem. O Firebird não trata a barra como escape.
- Não corrige dados de cargas já feitas.
- Não trata outros tipos que o `serialize_value` também converte mal. A análise encontrou três: `bytes` e `memoryview` (colunas `bytea` e BLOBs) viram o texto da representação Python, como `<memory at 0x...>`; `float('nan')` e `inf` saem sem aspas e o PostgreSQL lê como identificador; o caractere NUL não cabe em `text` no PostgreSQL. Cada um pede uma decisão própria e fica para outra change.

## Decisions

1. Manter `E'...'` e dobrar a barra.

   Um valor com barra vira `E'` + texto com `'` trocada por `''` e `\` trocada por `\\` + `'`. Numa string `E'...'` o PostgreSQL interpreta escapes com `standard_conforming_strings` ligado ou desligado, então `\\` vira sempre uma barra. As duas trocas são independentes, porque nenhuma introduz o caractere da outra.

   Alternativa considerada: string comum `'...'` sem tratar a barra, como o `pg_dump` grava. Com `standard_conforming_strings = on`, padrão desde o PostgreSQL 9.1, a barra é literal e o resultado seria o mesmo. Foi rejeitada porque o valor gravado passaria a depender da sessão que aplica o arquivo. O `Aplicar.py` lê a configuração do servidor, mas o `rodar2.sh` e um `psql` manual não passam pelo Hermes. Num banco com a configuração desligada, a barra voltaria a ser escape e os dados mudariam em silêncio, só com um warning no log.

2. Strings sem barra continuam em `'...'`.

   Só os comandos com barra mudam. A diferença entre duas execuções geradas antes e depois da correção fica pequena e fácil de revisar.

3. Corrigir o teste existente.

   O teste `sql_value(r'a\b') == r"E'a\b'"` em `tests/test_strategies.py` descreve o defeito: `E'a\b'` é `a` seguido de backspace. Ele passa a esperar `E'a\\b'`.

## Risks / Trade-offs

- Um script de cliente que dependia da interpretação, por exemplo passando `\n` ao `sql_value()` para gerar quebra de linha, muda de resultado. Mitigação: procurar nos clientes privados chamadas de `sql_value()` e `serialize_value()` com barra invertida antes da próxima carga.
- Dados já carregados com o defeito continuam alterados no destino. Ver Open Questions.
- Literais com barra ficam maiores no arquivo (`E'C:\\pasta'`). Aceito.

## Migration Plan

1. Corrigir o dialeto e os testes.
2. Validar contra PostgreSQL 16 com `standard_conforming_strings` ligado e desligado.
3. Gerar uma execução de um cliente com dados que tenham barra invertida e comparar com uma execução anterior: só os literais com barra devem mudar.

Rollback: reverter o commit. Nenhum formato de arquivo nem contrato muda.

## Open Questions

- Algum cliente já migrado tem texto com barra invertida em tabelas carregadas pelo `03`? Se tiver, o destino guarda o valor alterado. Aí é preciso decidir se essas tabelas serão reprocessadas. Uma consulta na V1 pelas colunas de texto com `\` nessas tabelas responde.
- Algum script de cliente privado monta texto com `\n` ou outra sequência esperando que o PostgreSQL a interprete?
