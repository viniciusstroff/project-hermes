# Catálogo de Estratégias de Campo

Estratégias ficam em [core/strategies.py](../core/strategies.py). Cada uma representa a transformação de **um campo**: como selecioná-lo da V1 e qual valor Python inserir na V2.

## Interface comum

Toda estratégia expõe três membros:

| Membro | Tipo | Descrição |
|---|---|---|
| `select_columns` | `list[str]` | Colunas a incluir no `SELECT` da V1. `Fixed` retorna `[]`. |
| `insert_col` | `str` | Nome da coluna no `INSERT` da V2. |
| `raw_value(row)` | qualquer valor Python | Valor que vai para a coluna de destino. |

Nenhuma estratégia gera SQL. O `TableMigration`, o `ExpandMigration` e o `MultiTargetMigration` montam o `SELECT`, pedem o `raw_value()` de cada estratégia e entregam os valores ao adapter de destino, que serializa no formato do banco. Um objeto de campo sem `select_columns`, `insert_col` ou `raw_value` é recusado com `TypeError` quando a migração é criada.

As estratégias se dividem em dois grupos:

- **portáveis**: a regra roda em Python e a mesma migração funciona com qualquer origem;
- **dependentes de dialeto**: carregam SQL de um banco específico e só funcionam com ele.

---

## Estratégias portáveis

### `Copy`: cópia direta

Copia um campo com o mesmo nome na V1 e na V2.

```python
Copy('f_id')
Copy('f_name')
```

**Use quando:** o nome e o tipo do campo são iguais nas duas versões.

### `Rename`: renomeação de campo

Lê `v1_name` da V1 e insere em `v2_name` na V2.

```python
Rename('f_active', 'f_status')
Rename('numeroprocesso', 'numero_processo')
```

**Use quando:** o campo mudou de nome entre V1 e V2, mas o valor não precisa de transformação.

### `Fixed`: valor fixo

Insere o mesmo valor em todas as linhas. Não aparece no `SELECT`.

```python
Fixed('f_create_user', 814)
Fixed('f_source', 'migration')
Fixed('f_active', True)
```

**Use quando:** a V2 exige uma coluna que não existe na V1 ou precisa de um valor padrão para todas as linhas migradas. Com um `SqlLiteral`, o `Fixed` vira dependente de dialeto (ver abaixo).

### `Lookup`: resolução por dicionário

Converte um valor da V1 usando um dicionário. Útil para `varchar` V1 → FK inteira V2.

```python
Lookup('f_state', {'SP': 25, 'RJ': 19, 'MG': 13}, fallback=None)

# Com renomeação
Lookup('f_area_v1', areas_map, v2_name='f_area', fallback=1)

# Com normalização da chave de entrada
Lookup('f_state', states_map, fallback=None, key_normalizer=lambda v: str(v).strip().upper())
```

| Parâmetro | Obrigatório | Descrição |
|---|---|---|
| `name` | sim | Nome do campo na V1 |
| `lookup_dict` | sim | Dicionário de mapeamento |
| `v2_name` | não | Nome na V2 (padrão: igual ao da V1) |
| `fallback` | não | Valor quando a chave não for encontrada (padrão: `None` → `NULL`) |
| `key_normalizer` | não | Função para normalizar a chave lida da V1 antes do `.get()` |

**Use quando:** um campo da V1 tem valores discretos que precisam ser convertidos para IDs ou valores diferentes na V2.

### `Transform`: transformação por callback

Aplica uma função Python ao valor lido da V1. A função recebe o valor e a linha inteira.

```python
Transform('f_process_id', lambda value, row: None if value == 0 else value, v2_name='f_process')
Transform('f_name', lambda value, row: value.strip())
```

**Use quando:** a regra é simples, depende de um campo e não tem estratégia pronta. Também serve para combinar regras, por exemplo limpar e depois truncar.

### `RegexClean`: limpeza por expressão regular

Aplica `re.sub(pattern, replacement, value)` e devolve o resultado como está. `None` continua `None`.

```python
RegexClean('f_cpf', r'[^\d]')                        # remove tudo que não é dígito
RegexClean('f_phone', r'[\s\-\(\)]', '')             # remove formatação de telefone
RegexClean('f_text', r'[\x00-\x08\x0b-\x1f]')        # remove caracteres de controle
RegexClean('f_doc_v1', r'[^\d]', v2_name='f_doc')    # limpa e renomeia
```

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `name` | obrigatório | Campo na V1 |
| `pattern` | obrigatório | Padrão regex para `re.sub` |
| `replacement` | `''` | Substituto (padrão: remoção) |
| `v2_name` | igual a `name` | Campo na V2 |

A estratégia só remove o que o padrão manda. Uma barra invertida que o padrão mantém chega ao destino.

**Use quando:** o valor da V1 contém caracteres inválidos, formatação ou ruído que precisa ser removido antes da carga.

### `CpfClean`: limpeza de CPF

Remove formatação de CPF, mantendo só os dígitos. Texto vazio continua vazio.

```python
CpfClean('f_cpf')
CpfClean('f_documento', 'f_cpf')
```

**Use quando:** o campo contém CPF com pontos, traços ou barras e a V2 espera apenas dígitos.

### `PhoneClean`: limpeza de telefone

Remove formatação de telefone, mantendo apenas dígitos e `+`.

```python
PhoneClean('f_phone')
PhoneClean('f_mobile2', 'f_number1')
```

**Use quando:** telefone, celular ou fax chegam com parênteses, traços ou espaços e a V2 espera valor limpo.

### `DateConvert`: texto para data

Faz o parse com `from_fmt` e devolve `datetime.date`, ou `datetime.datetime` com `as_datetime=True`. Valor vazio ou inválido vira `None`. O formato gravado no destino é decidido pelo adapter: no PostgreSQL, `'2026-06-06'` e `'2026-06-06 10:30:00'`.

```python
DateConvert('f_nascimento')                                              # DD/MM/YYYY
DateConvert('f_created_at', '%Y%m%d')                                    # YYYYMMDD
DateConvert('f_criado', '%d/%m/%Y %H:%M', as_datetime=True)              # com hora
DateConvert('dt_nasc', v2_name='birth_date')                             # com renomeação
```

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `name` | obrigatório | Campo na V1 |
| `from_fmt` | `'%d/%m/%Y'` | Formato da V1 |
| `v2_name` | igual a `name` | Campo na V2, só por nome |
| `as_datetime` | `False` | Devolve data e hora em vez de só a data |

O antigo `to_fmt` saiu. Para gravar a data como texto num formato próprio, use `Transform`.

**Use quando:** a V1 armazena datas em texto.

### `EmailExtract`: extração de e-mail

Extrai o primeiro endereço de e-mail do valor. Devolve `None` se não encontrar nenhum.

```python
EmailExtract('f_email')
EmailExtract('f_contact')  # campo pode conter texto + email misturado
```

**Use quando:** o campo da V1 armazena texto livre que às vezes contém um e-mail, e a V2 espera apenas o endereço limpo.

### `NullIfBlank`, `Truncate`, `Upper`, `Lower` e `Default`

Cobrem as expressões SQL mais comuns no `SELECT` da origem, com a regra em Python. Todas aceitam `v2_name`.

| Estratégia | Substitui | Regra |
|---|---|---|
| `NullIfBlank('f_cpf')` | `NULLIF(f_cpf, '')` | `None` para `None`, texto vazio ou só com espaços |
| `Truncate('f_name', 80)` | `substring(f_name FROM 1 FOR 80)` | corta texto em 80 caracteres; outros tipos passam sem mudança |
| `Upper('f_uf')` | `upper(f_uf)` | `str.upper()` em texto; `None` continua `None` |
| `Lower('f_email')` | `lower(f_email)` | `str.lower()` em texto; `None` continua `None` |
| `Default('f_type', 1)` | `COALESCE(f_type, 1)` | troca `None` pelo valor |

A regra em Python não é idêntica à função SQL em alguns casos:

- o `NullIfBlank` trata texto só com espaços como vazio, o `NULLIF(col, '')` não;
- o `str.upper()` troca `ß` por `SS`; o `upper()` do PostgreSQL depende da collation;
- o `Truncate` conta caracteres, como o `substring` do PostgreSQL em UTF-8.

Quando o comportamento exato do banco importa, use `SourceSqlExpression`.

---

## Estratégias dependentes de dialeto

### `SourceSqlExpression`: expressão SQL no `SELECT` da origem

Injeta uma expressão no `SELECT`, escrita para um banco declarado em `dialect`, que é obrigatório.

```python
SourceSqlExpression("regexp_replace(f_doc, '[^0-9]', '', 'g')", 'f_doc', dialect='postgres')
SourceSqlExpression("COALESCE(f_type, 1)", 'f_type', dialect='firebird')
```

Antes de qualquer consulta, inclusive a contagem de progresso, a migração compara o `dialect` com o dialeto do adapter de origem (`postgres` ou `firebird`). Se não bater, ela levanta `ValueError` com a coluna, o dialeto esperado e o encontrado. Nada chega à origem.

O antigo `SqlExpression` saiu. Os usos documentados dele (`COALESCE`, `NULLIF`, `substring`) têm equivalentes portáveis acima.

**Use quando:** a regra só existe no banco de origem ou precisa do comportamento exato dele.

### `Fixed` com `SqlLiteral`: SQL no destino

Um `SqlLiteral` vai para o destino sem ser serializado.

```python
from core.sql import SqlLiteral

Fixed('f_created_at', SqlLiteral('CURRENT_TIMESTAMP'))
```

Vale o mesmo para uma estratégia própria cujo `raw_value()` devolve um `SqlLiteral`. O texto é SQL do destino e só funciona nele.

### Mapas de referência com consulta própria

A declaração de mapa de referência com `query=`, prevista na change `padronizar-lookups-de-referencia`, também é dependente de dialeto: a consulta é SQL do destino.

---

## Combinando estratégias

Todas as estratégias funcionam juntas na mesma `TableMigration`:

```python
TableMigration(
    source_sql='SELECT {fields} FROM public.acme_v1_customers {limit}',
    target='public.acme_customers_demo',
    fields=[
        Copy('f_id'),
        Truncate('f_name', 80),
        Rename('f_active', 'f_status'),
        Lookup('f_area', areas_map, fallback=1),
        DateConvert('f_birthday'),
        EmailExtract('f_email'),
        CpfClean('f_cpf'),
        Default('f_type', 1),
        Fixed('f_create_user', 814),
    ],
).run(self.context.engine('pg_v1'))
```

O `{fields}` no `source_sql` é substituído automaticamente pelas colunas de `select_columns` de cada estratégia (exceto `Fixed`, que não contribui).

---

## Quando escrever um método manual

Nem tudo cabe em `TableMigration`. Use SQL direto em `write_sql()` quando:

- O valor de destino depende de múltiplas colunas de formas não-triviais.
- A migração envolve lógica condicional por linha (ex: `if row['tipo'] == 'X': ...`).
- Há `UPDATE`s na V2 após a carga (não `INSERT`s).

Nesses casos, crie um método `insert_*` ou `update_*` na classe do Acme com a lógica manual, por exemplo `insert_acme_orders` ou `update_acme_customer_status`. Para montar valores nesse SQL, use `self.sql_value(valor)`, que serializa pelo adapter de destino configurado.

---

## Serialização

Quem transforma o valor Python em SQL é o adapter de destino. No PostgreSQL:

| Tipo Python | Saída SQL |
|---|---|
| `None` | `NULL` |
| `bool` | `true` / `false` |
| `int`, `float` | número literal |
| `str` com `\` | `E'...'` com a barra dobrada e `'` duplicado |
| `str` sem `\` | `'...'` com `'` duplicado |
| `date`, `datetime` | `'2026-06-06'`, `'2026-06-06 10:30:00'` |
| `SqlLiteral` | o texto, sem mudança |

Os tipos que o dialeto ainda não trata (`bytes`, `NaN`, caractere NUL) estão na change `serializar-tipos-especiais-no-dialeto`.
