# Catálogo de Estratégias de Campo

Estratégias ficam em [core/strategies.py](../core/strategies.py). Cada uma representa a transformação de **um campo**: como selecioná-lo da V1 e qual valor Python inserir na V2.

## Interface comum

Toda estratégia expõe três membros:

| Membro | Tipo | Descrição |
|---|---|---|
| `select_columns` | `list` | Campos a ler da origem, por nome ou como `NativeField`. `Fixed` retorna `[]`. |
| `insert_col` | `str` | Nome da coluna no `INSERT` da V2. |
| `raw_value(row)` | qualquer valor Python | Valor que vai para a coluna de destino. |

Nenhuma estratégia gera SQL. O `TableMigration`, o `ExpandMigration` e o `MultiTargetMigration` montam a consulta da origem com os campos das estratégias, pedem o `raw_value()` de cada estratégia e entregam os valores ao adapter de destino, que serializa no formato do banco. Um objeto de campo sem `select_columns`, `insert_col` ou `raw_value` é recusado com `TypeError` quando a migração é criada.

As estratégias se dividem em dois grupos:

- **portáveis**: a regra roda em Python e a mesma migração funciona com qualquer origem;
- **presas a um banco**: carregam código de um banco específico e só funcionam com ele.

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

Converte um valor da V1 usando um dicionário. Útil para `varchar` V1 → FK inteira V2. O dicionário pode ser escrito no script ou vir de uma tabela da V2, como mapa de referência.

```python
Lookup('f_state', {'SP': 25, 'RJ': 19, 'MG': 13}, fallback=None)

# Com renomeação
Lookup('f_area_v1', areas_map, v2_name='f_area', fallback=1)
```

Quando os valores estão numa tabela da V2, declare o mapa em `declare_reference_maps()` e leia com `self.reference_map(nome)`:

```python
from core.references import ReferenceMap

def declare_reference_maps(self):
    return [
        ReferenceMap(
            'states_by_code',
            table='public.states',
            key='f_code',
            value='f_id',
            filters={'f_active': True},
            key_normalizer=lambda v: str(v).strip().upper(),
        ),
    ]

# no método de carga
Lookup('f_state', self.reference_map('states_by_code'), fallback=None)
```

O `Lookup` reaproveita o `key_normalizer` do mapa: `' sp '` encontra a chave `'SP'` sem repetir a regra. Um `key_normalizer` passado ao `Lookup` vale no lugar do normalizador do mapa.

| Parâmetro | Obrigatório | Descrição |
|---|---|---|
| `name` | sim | Nome do campo na V1 |
| `lookup_dict` | sim | Dicionário ou mapa de referência carregado |
| `v2_name` | não | Nome na V2 (padrão: igual ao da V1) |
| `fallback` | não | Valor quando a chave não for encontrada (padrão: `None` → `NULL`) |
| `key_normalizer` | não | Função para normalizar a chave lida da V1. Com mapa de referência, o padrão é o normalizador do mapa |

Campos do `ReferenceMap`:

| Campo | Uso |
|---|---|
| `name` | nome único do mapa na execução |
| `table`, `key`, `value` | tabela da V2 e colunas da chave e do valor |
| `filters` | igualdades combinadas com `E`; `None` vira `IS NULL` |
| `query` | SQL da V2 que devolve chave e valor, no lugar de `table` |
| `key_normalizer`, `value_normalizer` | funções aplicadas a cada linha; a chave usa `str(v).strip()` por padrão |
| `skip_null_keys` | descarta linhas com chave nula (padrão `True`) |

O runner carrega os mapas declarados antes de abrir a primeira fase e registra no log cada um com a quantidade de chaves. Depois disso a geração não consulta mais a V2. Viram erro, antes de qualquer fase: nome repetido, declaração com `table` e `query` juntos ou sem nenhum dos dois e chave que, depois de normalizada, aparece com dois valores diferentes. Pedir um mapa que não foi declarado também é erro. Um mapa vazio gera só um aviso no log.

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

Quando o comportamento exato do banco importa, use `NativeField`.

---

## Estratégias presas a um banco

### `NativeField`: campo calculado na origem

Fica em `core/queries.py`. Lê uma expressão na linguagem de um adapter de origem com o nome `alias` e grava o valor na coluna de destino de mesmo nome. O adapter é o primeiro argumento e é obrigatório.

```python
from core.queries import NativeField

NativeField('postgres', "regexp_replace(f_doc, '[^0-9]', '', 'g')", 'f_doc')
NativeField('firebird', 'COALESCE(f_type, 1)', 'f_type')
```

Antes de qualquer consulta, inclusive a contagem de progresso, o adapter de origem compara o adapter declarado com o próprio nome. Se não bater, ele levanta `NativeAdapterMismatchError` (um `ValueError`) com o campo e os dois adapters. Nada chega à origem.

O `SourceSqlExpression` saiu. O `NativeField` faz o mesmo papel, preso ao adapter e não ao dialeto.

**Use quando:** a regra só existe no banco de origem ou precisa do comportamento exato dele. Uma consulta inteira que não cabe num `SourceQuery`, como uma junção, vai em `NativeQuery` (ver [Combinando estratégias](#combinando-estratégias)).

### `Fixed` com `SqlLiteral`: SQL no destino

Um `SqlLiteral` vai para o destino sem ser serializado.

```python
from core.sql import SqlLiteral

Fixed('f_created_at', SqlLiteral('CURRENT_TIMESTAMP'))
```

Vale o mesmo para uma estratégia própria cujo `raw_value()` devolve um `SqlLiteral`. O texto é SQL do destino e só funciona nele.

### Mapas de referência com consulta própria

Um `ReferenceMap` com `query=` também fica preso a um banco: a consulta é uma `NativeQuery` do adapter de destino. Um texto solto é recusado. A primeira coluna é a chave e a segunda é o valor.

```python
ReferenceMap(
    'states_by_country',
    query=NativeQuery(
        'postgres',
        "SELECT s.f_code || '-' || c.f_code, s.f_id FROM public.states s JOIN public.countries c ON c.f_id = s.f_country",
    ),
)
```

---

## Combinando estratégias

Todas as estratégias funcionam juntas na mesma `TableMigration`:

```python
TableMigration(
    source=SourceQuery('public.acme_v1_customers'),
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
).run(self.context.source('v1'))
```

O `source` é um `SourceQuery` ou um `NativeQuery`, os dois de `core/queries.py`:

- `SourceQuery(entity, fields=(), where={}, order_by=(), limit=None)` lê uma tabela ou coleção. Sem `fields`, lê os campos de `select_columns` de cada estratégia (o `Fixed` não contribui). O `where` só tem igualdade, combinada com E. Um valor `None` vira `IS NULL`. O adapter de origem traduz tudo para a linguagem dele e põe aspas nos nomes que precisam, como palavras reservadas.
  Uma `condition` de `TargetMigration` que lê um campo que nenhuma estratégia pede não recebe esse campo. Nesse caso, liste em `fields` todos os campos da leitura.
- `NativeQuery(adapter, query, limit=None)` leva uma consulta inteira na linguagem de um adapter, para o que não cabe no `SourceQuery`, como uma junção. O texto não tem marcador de limite: no modo debug, o adapter envolve a consulta com o limite dele. Uma `NativeQuery` de outro adapter é recusada antes de qualquer leitura.

```python
SourceQuery('public.tb_curso', order_by='cod_curso')
SourceQuery('public.pedidos', where={'f_status': 1, 'f_excluido': None})
NativeQuery('postgres', 'SELECT p.cod_prof, a.cod_aluno FROM tb_professor p LEFT JOIN tb_aluno a ON a.cpf = p.cpf')
```

Numa origem MongoDB, a `NativeQuery` é um pipeline de agregação e o `NativeField` é uma expressão de agregação. Um campo com ponto (`endereco.cidade`) é lido pelo caminho e volta com a chave `endereco.cidade`. Numa origem relacional, o campo com ponto é recusado.

```python
NativeQuery('mongodb', {'collection': 'tb_aluno', 'pipeline': [{'$match': {'situacao': 'A'}}]})
NativeField('mongodb', {'$toUpper': '$sigla'}, 'sigla')
SourceQuery('tb_aluno', fields=('cod_aluno', 'endereco.cidade'))
```

O modo debug (`--debug 100`) é um limite inteiro que o contexto aplica a toda consulta. Quando a consulta já tem um limite menor, vale o menor.

---

## Quando escrever um método manual

Nem tudo cabe em `TableMigration`. Escreva um método `insert_*` ou `update_*` na classe do Acme quando:

- O valor de destino depende de múltiplas colunas de formas não-triviais.
- A migração envolve lógica condicional por linha (ex: `if row['tipo'] == 'X': ...`).
- Há `UPDATE`s na V2 após a carga (não `INSERT`s).

O método lê a origem pelo engine e emite operações, sem montar SQL:

```python
from core.operations import Insert, Native

engine = self.context.source('v1')
for row in engine.read(SourceQuery('public.pedidos', order_by='f_id')):
    if row['f_tipo'] == 'X':
        self.emit(Insert('public.pedidos', {'f_id': row['f_id'], 'f_valor': row['f_total']}))
```

O adapter de destino serializa os valores do `Insert`. Um comando que só existe num banco, como um `UPDATE` com junção, vai em `Native('postgres', sql)`. Ele é recusado por qualquer outro destino.

---

## Serialização

Quem transforma o valor Python em SQL é o adapter de destino. O dialeto PostgreSQL aceita uma lista fechada de tipos:

| Tipo Python | Saída SQL |
|---|---|
| `None` | `NULL` |
| `bool` | `true` / `false` |
| `int`, `float` | número literal |
| `float` especial | `'NaN'::float8`, `'Infinity'::float8`, `'-Infinity'::float8` |
| `Decimal` | número sem aspas, como `85.00` ou `1E+2` |
| `Decimal` especial | `'NaN'::numeric`, `'Infinity'::numeric`, `'-Infinity'::numeric` |
| `str` com `\` | `E'...'` com a barra dobrada e `'` duplicado |
| `str` sem `\` | `'...'` com `'` duplicado |
| `date` | `'2026-06-06'` |
| `datetime` | `'2026-06-06 10:30:00'`, com o fuso quando houver: `'2026-06-06 10:30:00-03:00'` |
| `time` | `'10:30:00'` |
| `UUID` | `'12345678-1234-5678-1234-567812345678'` |
| `bytes`, `bytearray`, `memoryview` | `decode('4142', 'hex')` |
| `SqlLiteral` | o texto, sem mudança |

`'Infinity'::numeric` só existe a partir do PostgreSQL 14.

Qualquer outro tipo (`list`, `dict`, `timedelta` e afins) e texto com o caractere NUL fazem a geração falhar. O erro cita a tabela de destino, a coluna e o tipo Python. No caso do NUL, cita também a posição do caractere. O valor não aparece na mensagem, que vai para o log e para o manifesto. O `--debug` costuma achar esses casos antes de uma geração completa.

O PostgreSQL não grava NUL em texto. A decisão de remover o caractere, trocá-lo ou descartar a linha é do cliente. A correção mais comum:

```python
Transform('f_obs', lambda value, row: value.replace('\x00', '') if value else value)
```

Um BLOB de texto lido sem charset chega como `bytes`. Numa coluna `text`, o PostgreSQL aceita o `decode(...)` e grava a forma hexadecimal (`\x4142`), sem erro. Por isso a geração registra no log um aviso, uma vez por tabela e coluna, quando uma coluna recebe valor binário. Se a coluna é de texto, decodifique na strategy:

```python
Transform('f_texto', lambda value, row: bytes(value).decode('latin-1') if value is not None else None)
```
