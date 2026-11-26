# Conversão de clientes para o core desacoplado

A change `desacoplar-migracao-de-banco-e-versao` trocou a configuração e a API do core sem manter aliases, porque o projeto não está em produção. Um cliente escrito antes dela não roda sem ajuste. Este guia leva um cliente privado do formato antigo ao novo, usando a AcmeEdu como exemplo.

A conversão tem quatro partes: o `.env`, os hooks do script, as leituras e a aplicação. Depois de cada parte, rode `python3 Migracao.py <Cliente> --debug 100 --dry-run` e corrija o que a mensagem de erro apontar. As mensagens dizem a chave, o adapter ou o hook envolvido.

## 1. O `.env`

As chaves fixas `PG_V1_*`, `PG_V2_*` e `FB_V1_*` saíram. Cada conexão tem um nome e um adapter.

Antes:

```dotenv
PG_V1_HOST=acmeedu-v1
PG_V1_PORT=5432
PG_V1_NAME=acmeedu_v1
PG_V1_USER=acmeedu
PG_V1_PASS=acmeedu
PG_V2_HOST=acmeedu-v2
PG_V2_PORT=5432
PG_V2_NAME=acmeedu_v2
PG_V2_USER=acmeedu
PG_V2_PASS=acmeedu
```

Depois:

```dotenv
SOURCE_V1_ADAPTER=postgres
SOURCE_V1_HOST=acmeedu-v1
SOURCE_V1_PORT=5432
SOURCE_V1_NAME=acmeedu_v1
SOURCE_V1_USER=acmeedu
SOURCE_V1_PASS=acmeedu

TARGET_ADAPTER=postgres
TARGET_HOST=acmeedu-v2
TARGET_PORT=5432
TARGET_NAME=acmeedu_v2
TARGET_USER=acmeedu
TARGET_PASS=acmeedu
```

| Antes | Depois |
|---|---|
| `PG_V1_*` | `SOURCE_V1_ADAPTER=postgres` e `SOURCE_V1_*` |
| `FB_V1_*` | `SOURCE_FB_ADAPTER=firebird` e `SOURCE_FB_*` (o nome `FB` é livre) |
| `PG_V2_*` e `TARGET_DB_*` no `.env` do cliente | `TARGET_ADAPTER=postgres` e `TARGET_*` |
| `pg_dump` sempre, com o cliente 9.6 da imagem | `SOURCE_V1_NATIVE_COPY=pg_dump`, opcional, com a imagem construída com `PG_CLIENT_VERSION` |

O `TARGET_DB_*` do `.env` da raiz continua: ele só configura o PostgreSQL local do Docker Compose.

## 2. Os hooks do script

| Antes | Depois |
|---|---|
| `self.context.engine('pg_v1')` | `self.context.source('v1')`, com o nome do `.env` em minúsculas |
| `self.pg_v1_conn`, `self.pg_v2_conn`, `self.fb_v1_conn` | não existem: leia por `source(...).read(...)` ou pelos mapas de referência |
| `dump_ignoring_tables()` e `dump_non_existing_tables()` | `compatible_entities()`, uma lista de inclusão de `CopyEntity` |
| `self.write_sql('TRUNCATE ...')` | `self.clear([...])` |
| `self.write_sql(self.disable_triggers)` | `self.suspend_constraints()`; o runner reabilita no fim |
| `set_indexes_commands()` e `set_index_commands_for([...])` | saem, sem substituto |
| `self.write_reset_sequences(tabelas, column='id')` | `self.reset_identity(tabelas, column='id')` |
| `self.print_comment(texto)` | `self.comment(texto)` |
| `self.write_sql(sql)` com SQL próprio | `self.emit(Native('postgres', sql))` |
| `self.sql_value(valor)` | sai: emita `Insert(entidade, {coluna: valor})` com o valor Python |

Antes:

```python
def dump_ignoring_tables(self):
    return [f'public.{name}' for name in (*LEGACY_TABLES, *LEGACY_SEQUENCES)]

def dump_non_existing_tables(self):
    return []

def set_indexes_commands(self):
    self.set_index_commands_for(['people_name_idx', 'enrollments_class_idx'])

def run_initial(self):
    self.print_comment('LIMPEZA DAS TABELAS DA V2')
    tables = ', '.join(f'public.{name}' for name in [*reversed(TARGET_TABLES), 'holidays'])
    self.write_sql(f'TRUNCATE {tables} RESTART IDENTITY;\n')
    self.print_comment('DESABILITAR TRIGGERS DE TABELAS')
    self.write_sql(self.disable_triggers)
    self.print_comment('DESABILITAR ÍNDICES DE TABELAS')
    self.write_sql(self.disable_indexes)

def reset_sequences(self):
    self.write_reset_sequences(TARGET_TABLES, column='id')
```

Depois:

```python
def compatible_entities(self):
    return [CopyEntity('holidays', source='v1', order_by='id')]

def run_initial(self):
    self.comment('LIMPEZA DAS TABELAS DA V2')
    self.clear([*reversed(TARGET_TABLES), 'holidays'])
    self.comment('DESABILITAR TRIGGERS DE TABELAS')
    self.suspend_constraints()

def reset_sequences(self):
    self.reset_identity([*TARGET_TABLES, 'holidays'], column='id')
```

A lista de exclusão da AcmeEdu tinha 14 nomes para copiar uma tabela. A de inclusão tem uma. Uma tabela nova na V1 não entra mais sem ser listada.

O `reset_identity()` acha a sequence de cada coluna pelo `pg_get_serial_sequence`, sem supor o nome `<tabela>_f_id_seq`. Uma tabela copiada linha a linha (`CopyEntity`) não ganha `setval` do `pg_dump`, então ela também entra no `reset_identity()`.

O helper de índices saiu porque não funcionava: com nome de índice não fazia nada e com nome de tabela deixava o índice sem as linhas carregadas.

### Tarefas, opcional

Um `run_inserts()` que chama os `insert_*` em ordem continua funcionando. Para gerar em paralelo com `--workers`, troque o `run_inserts()` por `@task` em cada `insert_*`, na mesma ordem. Assim, o `03_load_transformed_data` sai igual ao de antes.

Antes:

```python
def run_inserts(self):
    self.insert_courses()
    self.insert_students()
```

Depois:

```python
from core.tasks import task

@task('courses')
def insert_courses(self): ...

@task('students')
def insert_students(self): ...
```

Uma tarefa que depende do resultado de outra, como um dicionário montado por ela, vai para uma sequência maior: `@task('pedidos', sequence=2)`. Dentro de uma tarefa, a origem é lida por `self.context.source(...)`. `clear()`, `suspend_constraints()` e `reset_identity()` não são aceitos nela. As regras completas estão em [novo-cliente.md](novo-cliente.md#passo-5--respeitar-a-ordem-das-inserções).

## 3. As leituras

O `source_sql` com `{fields}` e `{limit}` virou `source=`, com um `SourceQuery` para o caso comum e um `NativeQuery` para o que não cabe nele.

Antes:

```python
TableMigration(
    source_sql='SELECT {fields} FROM public.tb_curso ORDER BY cod_curso {limit}',
    target='public.courses',
    fields=[...],
).run(self.context.engine('pg_v1'))
```

Depois:

```python
TableMigration(
    source=SourceQuery('tb_curso', order_by='cod_curso'),
    target='courses',
    fields=[...],
).run(self.context.source('v1'))
```

O adapter monta a consulta com os campos que as estratégias pedem e aplica o limite do modo debug. Os nomes sem schema (`tb_curso`, `courses`) servem em qualquer banco: o PostgreSQL resolve pelo `search_path` e o Firebird não tem schema. Um cliente que só roda em PostgreSQL pode manter o `public.`.

| Antes | Depois |
|---|---|
| `source_sql='SELECT {fields} FROM t {limit}'` | `source=SourceQuery('t')` |
| filtro de igualdade no SQL | `SourceQuery('t', where={'f_ativo': True, 'f_excluido': None})` |
| junção, `LATERAL`, função do banco | `NativeQuery('postgres', sql)`, sem `{limit}` no texto |
| `SourceSqlExpression(expr, col, dialect='postgres')` | `NativeField('postgres', expr, col)` |
| `ReferenceMap(..., query='SELECT ...')` | `ReferenceMap(..., query=NativeQuery('postgres', 'SELECT ...'))` |
| `engine.v1_cursor()` e `engine.scalar(sql)` | `engine.read(query)` e `engine.count(query)` |

Uma `condition` de `TargetMigration` que lê um campo que nenhuma estratégia pede não recebe esse campo pelo `SourceQuery` sem `fields`. Nesse caso, liste todos os campos em `fields`.

A AcmeEdu tinha uma consulta com `LEFT JOIN LATERAL` para achar o aluno com o mesmo CPF de cada professor. Ela poderia virar uma `NativeQuery('postgres', ...)` e continuar funcionando em PostgreSQL. Para rodar em qualquer banco, o agrupamento foi para Python: o script lê os alunos com um `SourceQuery` e casa pelos dígitos do CPF (`TeacherPeople` no `AcmeEdu.py`). O SQL gerado ficou igual, fora o `public.`.

Use `NativeQuery` sem medo quando o cliente só roda num banco. Ela fica presa ao adapter declarado. Um adapter diferente recusa a consulta antes de ler qualquer coisa.

## 4. A aplicação

| Antes | Depois |
|---|---|
| arquivos em `runs/<cliente>/<timestamp>/sql/` | em `comandos/`, com a extensão do destino (`.sql` ou `.jsonl`) |
| fase `02_dump_compatible_tables.sql` | `02_copy_compatible_entities.sql`, que existe também no modo debug |
| `Aplicar.py` com `PGHOST`, `PGDATABASE`, `PGUSER` e `PGPASSWORD` | `Aplicar.py` com `TARGET_HOST`, `TARGET_NAME`, `TARGET_USER` e `TARGET_PASS` |
| `SQLSTATE` no `erros.txt` | `código <x>`, dado pelo adapter |
| `pip install -r requirements.txt` com todos os drivers | `requirements.txt` e o `requirements/<adapter>.txt` de cada banco usado |

O `Aplicar.py` lê do manifesto o adapter de destino e a lista de artefatos. Uma execução gerada antes da conversão não tem essas informações e precisa ser gerada de novo.

## Conferindo a conversão

1. `python3 Migracao.py <Cliente> --debug 100 --dry-run` termina em `rolled_back`.
2. A geração completa sem debug termina com status `success` no `manifest.json`.
3. Com o destino de homologação, `--dry-run` sem debug lista os erros de uma vez no `erros.txt`.
4. Compare os artefatos com os da versão antiga. Na AcmeEdu, o `00` e o `03` ficaram iguais, fora o `public.`. O `02` fica igual com a cópia nativa ligada. Com a cópia linha a linha, ele traz os mesmos feriados em `INSERT`s gerados pelo core. O `01` perdeu os comandos de índice. O `04` perdeu os comandos de índice, usa o `pg_get_serial_sequence` e ganhou o ajuste da sequence de `holidays`.

A AcmeEdu convertida roda com a V1 e a V2 em PostgreSQL de versões diferentes, em MongoDB e com a V1 em Firebird. Os resultados estão em [AcmeEdu/README.md](../AcmeEdu/README.md#matriz-de-prova).
