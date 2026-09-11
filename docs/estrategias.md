# Catálogo de Estratégias de Campo

Estratégias ficam em [core/strategies.py](../core/strategies.py). Cada uma representa a transformação de **um campo**: como selecioná-lo da V1 e qual valor produzir no `INSERT` da V2.

## Interface comum

Toda estratégia expõe três membros:

| Membro | Tipo | Descrição |
|---|---|---|
| `select_columns` | `list[str]` | Colunas a incluir no `SELECT` da V1. `Fixed` retorna `[]`. |
| `insert_col` | `str` | Nome da coluna no `INSERT` da V2. |
| `value(row)` | `str` | Valor SQL-escaped pronto para o `VALUES(...)`. |

O `TableMigration` itera sobre a lista de estratégias, monta o `SELECT` e o `INSERT` automaticamente.

---

## `Copy` — cópia direta

Copia um campo com o mesmo nome na V1 e na V2.

```python
Copy('f_id')
Copy('f_name')
```

**Use quando:** o nome e o tipo do campo são iguais nas duas versões.

---

## `Rename` — renomeação de campo

Lê `v1_name` da V1 e insere em `v2_name` na V2.

```python
Rename('f_active', 'f_status')
Rename('numeroprocesso', 'numero_processo')
```

**Use quando:** o campo mudou de nome entre V1 e V2, mas o valor não precisa de transformação.

---

## `Fixed` — valor literal fixo

Insere o mesmo valor em todas as linhas. Não aparece no `SELECT`.

```python
Fixed('f_create_user', 814)
Fixed('f_source', 'migration')
Fixed('f_active', True)
```

**Use quando:** a V2 exige uma coluna que não existe na V1 ou precisa de um valor padrão para todas as linhas migradas.

---

## `SqlExpression` — expressão SQL no `SELECT`

Injeta uma expressão SQL arbitrária no `SELECT` preservando o fluxo `{fields}`.

```python
SqlExpression("substring(CAST(f_name AS text) FROM 1 FOR 80)", 'f_name')
SqlExpression("COALESCE(f_type, 1)", 'f_type')
SqlExpression("NULLIF(f_cpf, '')", 'f_cpf')
```

**Use quando:** o valor precisa ser tratado no próprio SQL de origem, mas você quer continuar usando `TableMigration` sem escrever o `SELECT` inteiro manualmente.

---

## `Lookup` — resolução por dicionário

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

---

## `Transform` — transformação por callback

Aplica uma função Python ao valor lido da V1 antes de serializar para SQL.

```python
Transform('f_process_id', lambda value, row: None if value == 0 else value, v2_name='f_process')
Transform('f_name', lambda value, row: value.strip())
```

**Use quando:** a regra é simples, depende de um único campo, e não vale a pena escrever um método manual inteiro só para ajustar o valor.

---

## `RegexClean` — limpeza por expressão regular

Aplica `re.sub(pattern, replacement, value)` no valor antes de escapar.

```python
RegexClean('f_cpf', r'[^\d]')            # remove tudo que não é dígito
RegexClean('f_phone', r'[\s\-\(\)]', '')  # remove formatação de telefone
RegexClean('f_text', r'[\x00-\x08\x0b-\x1f]')  # remove caracteres de controle
```

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `name` | — | Campo na V1 e na V2 |
| `pattern` | — | Padrão regex para `re.sub` |
| `replacement` | `''` | Substituto (padrão: remoção) |

**Use quando:** o valor da V1 contém caracteres inválidos, formatação ou ruído que precisa ser removido antes da carga.

---

## `CpfClean` — limpeza de CPF

Remove formatação de CPF com uma estratégia nomeada.

```python
CpfClean('f_cpf')
CpfClean('f_documento', 'f_cpf')
```

**Use quando:** o campo contém CPF com pontos, traços ou barras e a V2 espera apenas dígitos.

---

## `PhoneClean` — limpeza de telefone

Remove formatação de telefone mantendo apenas dígitos e `+`.

```python
PhoneClean('f_phone')
PhoneClean('f_mobile2', 'f_number1')
```

**Use quando:** telefone, celular ou fax chegam com parênteses, traços ou espaços e a V2 espera valor limpo.

---

## `DateConvert` — conversão de formato de data

Faz parse com `from_fmt` e formata com `to_fmt`. Retorna `NULL` em caso de valor vazio ou inválido.

```python
DateConvert('f_nascimento')                                # DD/MM/YYYY → YYYY-MM-DD
DateConvert('f_created_at', '%Y%m%d', '%Y-%m-%d')         # YYYYMMDD → YYYY-MM-DD
```

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `name` | — | Nome do campo (mesmo na V1 e V2) |
| `from_fmt` | `'%d/%m/%Y'` | Formato da V1 |
| `to_fmt` | `'%Y-%m-%d'` | Formato da V2 |

**Use quando:** a V1 armazena datas em formato diferente do esperado pela V2.

---

## `EmailExtract` — extração de e-mail

Extrai o primeiro endereço de e-mail válido do valor via regex. Retorna `NULL` se não encontrar nenhum.

```python
EmailExtract('f_email')
EmailExtract('f_contact')  # campo pode conter texto + email misturado
```

**Use quando:** o campo da V1 armazena texto livre que às vezes contém um e-mail, e a V2 espera apenas o endereço limpo.

---

## Combinando estratégias

Todas as estratégias funcionam juntas na mesma `TableMigration`:

```python
TableMigration(
    source_sql='SELECT {fields} FROM public.acme_v1_customers {limit}',
    target='public.acme_customers_demo',
    fields=[
        Copy('f_id'),
        Copy('f_name'),
        Rename('f_active', 'f_status'),
        Lookup('f_area', areas_map, fallback=1),
        DateConvert('f_birthday'),
        EmailExtract('f_email'),
        CpfClean('f_cpf'),
        Fixed('f_create_user', 814),
    ],
).run(self._engine)
```

O `{fields}` no `source_sql` é substituído automaticamente pelas colunas de `select_columns` de cada estratégia (exceto `Fixed`, que não contribui).

---

## Quando escrever um método manual

Nem tudo cabe em `TableMigration`. Use SQL direto em `write_sql()` quando:

- O valor de destino depende de múltiplas colunas de formas não-triviais.
- A migração envolve lógica condicional por linha (ex: `if row['tipo'] == 'X': ...`).
- Há `UPDATE`s na V2 após a carga (não `INSERT`s).

Nesses casos, crie um método `insert_*` ou `update_*` na classe do Acme com a lógica manual, por exemplo `insert_acme_orders` ou `update_acme_customer_status`.

---

## Escaping

A função `sql_value()` (em `core/strategies.py`) é usada por todas as estratégias:

| Tipo Python | Saída SQL |
|---|---|
| `None` | `NULL` |
| `bool` | `true` / `false` |
| `int`, `float` | número literal |
| string com `\` | `E'...'` com escape |
| string simples | `'...'` com `'` duplicado |
