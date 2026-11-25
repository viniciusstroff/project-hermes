## Context

Depois da change `preservar-barra-invertida-nos-literais`, o `PostgresDialect.serialize_value` fica assim:

```python
if value is None:
    return 'NULL'
if isinstance(value, bool):
    return 'true' if value else 'false'
if isinstance(value, (int, float)):
    return str(value)
escaped = str(value).replace("'", "''")
if '\\' in escaped:
    return "E'" + escaped.replace('\\', '\\\\') + "'"
return "'" + escaped + "'"
```

Ele é o ponto único de serialização de valores para o destino. Passam por ele os INSERTs do `TableMigration`, do `ExpandMigration` e do `MultiTargetMigration` (pelo `PostgresTargetAdapter`) e o `BaseMigration.sql_value()`. Depois da change `padronizar-lookups-de-referencia`, também os filtros das declarações de mapa de referência.

O que chega até ele é o que a strategy devolve. Na maioria das colunas é o valor que o driver leu da origem: o `psycopg2` devolve `memoryview` para `bytea`, `Decimal` para `numeric`, `list` para arrays, `dict` ou `list` para `json` e `jsonb` e `timedelta` para `interval`. O `firebirdsql` devolve `bytes` para BLOB binário e `Decimal` para `NUMERIC`.

Quando um comando gerado falha, o erro aparece só na aplicação, no relatório do `SqlArtifactApplier` ou no log do `rodar2.sh`. Na geração não há sinal.

## Goals / Non-Goals

**Goals:**

- Todo valor serializado chega a uma coluna do tipo correspondente igual ao original, ou a geração falha. A exceção é binário em coluna de texto, que o dialeto não consegue detectar e por isso vira aviso no log (decisão 2).
- O erro de geração aponta a tabela de destino, a coluna e o tipo, para o cliente corrigir sem abrir o SQL gerado.
- Os literais de texto, inteiros, booleanos e datas não mudam.

**Non-Goals:**

- Não converter `list` e `dict` para array ou JSON. Sem conhecer o tipo da coluna de destino, uma `list` pode ser array ou JSON (ver Open Questions).
- Não converter `timedelta` para `interval`.
- Não remover o NUL automaticamente.
- Não mexer no `FirebirdDialect`, que só serve às consultas de origem.

## Decisions

1. Lista fechada de tipos. Tipo desconhecido é erro.

   O dialeto aceita `None`, `bool`, `int`, `float`, `Decimal`, `str`, `datetime.date`, `datetime.datetime`, `datetime.time`, `uuid.UUID`, `bytes`, `bytearray` e `memoryview`. O `SqlLiteral` continua passando pelo `PostgresTargetAdapter` sem chegar ao dialeto. Qualquer outro tipo levanta `UnsupportedValueError`, subclasse de `TypeError`, com o nome do tipo.

   A ordem das checagens importa: `bool` antes de `int`, porque `bool` é subclasse de `int`; `datetime` antes de `date`, pelo mesmo motivo.

   Alternativa considerada: manter o `str()` como último recurso e registrar um aviso no log. Rejeitada porque o aviso se perde no meio da geração e o valor errado chega ao destino do mesmo jeito.

2. Binários com `decode('<hex>', 'hex')`.

   `bytes`, `bytearray` e `memoryview` viram `decode('4142', 'hex')`. A expressão devolve `bytea` e não depende do `standard_conforming_strings`, porque o texto hexadecimal não tem barra.

   Alternativa considerada: o formato `'\x4142'`. Rejeitada porque a barra seria escape numa sessão com `standard_conforming_strings` desligado. A change da barra invertida decidiu não depender dessa configuração. O `E'\\x4142'::bytea` funciona nas duas configurações, mas é mais difícil de ler e não traz ganho.

   Se a coluna de destino for `text` e a origem devolver `bytes` (um BLOB de texto lido sem charset, por exemplo), a aplicação não falha. Testado no PostgreSQL 16: o cast de atribuição de `bytea` para `text` grava a forma hexadecimal, `\x4142`. Antes, o destino recebia `b'AB'`. Os dois estão errados para uma coluna de texto. O dialeto não conhece o tipo da coluna para recusar. Por isso a migration registra no log, uma vez por tabela e coluna, que a coluna recebeu valor binário. Quem esperava texto ali vê o aviso e corrige com `Transform` e `.decode()` no charset certo.

3. Valores numéricos especiais com cast explícito.

   | Valor Python | Literal |
   |---|---|
   | `float('nan')` | `'NaN'::float8` |
   | `float('inf')` | `'Infinity'::float8` |
   | `float('-inf')` | `'-Infinity'::float8` |
   | `Decimal('NaN')` | `'NaN'::numeric` |
   | `Decimal('Infinity')` | `'Infinity'::numeric` |
   | `Decimal('85.00')` | `85.00` |

   O cast explícito mantém o valor numérico em qualquer contexto, inclusive num filtro de mapa de referência. Numa coluna `numeric`, o PostgreSQL aplica o cast de atribuição de `float8` para `numeric`.

   O `Decimal` finito passa a sair sem aspas, como `int` e `float`. Hoje ele sai como `'85.00'` e depende da conversão do literal sem tipo. O `str()` do `Decimal` pode usar expoente (`1E+2`), que o PostgreSQL aceita como constante numérica.

   `'Infinity'::numeric` só existe a partir do PostgreSQL 14. Num destino mais antigo, a aplicação falha nesse comando (ver Open Questions).

4. Tipos temporais em ISO 8601.

   `date` vira `'2026-06-06'`. `datetime` vira `isoformat(sep=' ')`, como `'2026-06-06 10:30:00'` ou `'2026-06-06 10:30:00-03:00'` com fuso. `time` vira `isoformat()`. Para `date` e `datetime`, é o mesmo texto que o `str()` gera hoje. A regra fica explícita para que outro dialeto saiba o que implementar e para que os testes fixem o formato. A change `revisar-estrategias-dependentes-de-dialeto` faz o `DateConvert` devolver `date` e depende desse formato.

   `UUID` vira o texto canônico entre aspas, como hoje.

5. NUL é erro. A decisão fica com o cliente.

   Uma string com `\x00` levanta `UnsupportedValueError` dizendo que o PostgreSQL não grava NUL em texto. O dialeto não tem como saber se o caractere deve sair, virar espaço ou se a linha deve ser descartada. A documentação mostra a correção mais comum:

   ```python
   Transform('f_obs', lambda value, row: value.replace('\x00', '') if value else value)
   ```

   Alternativa considerada: remover o NUL no dialeto, em silêncio. Rejeitada porque muda o dado sem aviso, que é o tipo de problema que esta change existe para evitar.

6. Erro com a coluna e sem o valor.

   O `_field_value()` do `TableMigration` e do `MultiTargetMigration` captura o `UnsupportedValueError` e levanta `ValueSerializationError` com a tabela de destino, a coluna, o tipo Python e o motivo. Para NUL, inclui a posição do caractere. A mensagem não inclui o valor, porque vai para o log e para o manifesto. A coluna pode ter dado pessoal. O runner já transforma a exceção em manifesto `failed`.

   O `--debug` roda as mesmas strategies sobre uma amostra da origem, então costuma achar o problema antes de uma geração completa.

## Risks / Trade-offs

- Uma geração que hoje termina passa a falhar onde havia valor errado. Mitigação: é o comportamento pretendido. O erro diz a coluna e a correção costuma ser um `Transform`.
- BLOBs grandes dobram de tamanho no arquivo, por causa do hexadecimal. Um BLOB de 10 MB vira uma linha de 20 MB no `03_load_transformed_data.sql`. O separador do aplicador lê por linha e aguenta, mas o arquivo cresce. Se ficar pesado, o caminho é o executor `bulk-copy` do item 11.
- `'Infinity'::numeric` falha em destino anterior ao PostgreSQL 14.
- Binário numa coluna de texto continua gravando um valor errado, agora `\x4142` em vez de `b'AB'`. Mitigação: o aviso no log por tabela e coluna. A conferência de tipo só seria possível lendo o esquema do destino, o que a geração evita depois da change `padronizar-lookups-de-referencia`.
- Sem o valor na mensagem, achar a linha exige consultar a origem pela coluna indicada. Aceito para manter dado pessoal fora do log.

## Migration Plan

1. Confirmar que `preservar-barra-invertida-nos-literais` está na `main`.
2. Implementar as regras no `PostgresDialect` com testes por tipo.
3. Adicionar o contexto do erro nas migrations.
4. Gerar uma execução antes e depois (com o `AcmeEdu`, se ele já existir) e comparar: só os literais de `Decimal` devem mudar, a menos que a origem tenha os tipos que antes saíam errados.
5. Atualizar a documentação.

Rollback: reverter o commit. Nenhum formato de arquivo muda.

## Open Questions

- Algum cliente privado tem colunas `json`, `jsonb` ou array em tabelas transformadas? Se tiver, vale uma strategy `Json` que faça `json.dumps` e um tratamento de array, em vez de deixar cada cliente escrever o seu `Transform`.
- Algum destino roda PostgreSQL anterior ao 14? Se rodar, `Decimal('Infinity')` precisa virar erro na geração.
- Se o NUL aparecer em mais de um cliente, vale uma strategy própria para removê-lo, combinável com as outras.
