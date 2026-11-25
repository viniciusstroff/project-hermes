# Criando a migração de um novo Acme

Este documento descreve o que é necessário para adicionar um novo escritório ao sistema. O objetivo é ter um checklist que pode ser seguido do zero até a primeira execução.

O [AcmeEdu](../AcmeEdu/README.md) segue este checklist e roda de ponta a ponta com bases fictícias em Docker. Serve de exemplo completo de `TableMigration`, `MultiTargetMigration`, mapa de referência lido da V2, agrupamento e separação de dados.

---

## Estrutura esperada

```
Migracao/
└── NomeAcme/
    ├── NomeAcme.py   ← script de migração
    ├── .env             ← credenciais e caminhos (nunca comitar)
    └── .env.example     ← versão sem valores reais (comitar)
```

O nome do diretório e do arquivo `.py` devem ser idênticos (case-sensitive). Esse nome é passado como argumento para `python3 Migracao.py <NomeAcme>`.

Os artefatos de execução são criados automaticamente em `runs/<NomeAcme>/<YYYYmmdd-HHMMSS>/`.

---

## Passo 1 — Criar o diretório do cliente

```bash
mkdir -p NomeAcme
```

---

## Passo 2 — Criar o `.env`

Copie de um Acme existente e ajuste:

```bash
cp Acme/.env.example NomeAcme/.env
```

Cada conexão tem nome e adapter. Uma origem é um grupo `SOURCE_<NOME>_*` com `SOURCE_<NOME>_ADAPTER`. O nome em minúsculas é o que a migração pede em `self.context.source('<nome>')`. O destino é `TARGET_ADAPTER` com as chaves `TARGET_*`. Os adapters `postgres` e `firebird` exigem `HOST`, `NAME` e `USER`.

```dotenv
# Origem v1: base legada PostgreSQL
SOURCE_V1_ADAPTER=postgres
SOURCE_V1_HOST=
SOURCE_V1_PORT=5432
SOURCE_V1_NAME=
SOURCE_V1_USER=
SOURCE_V1_PASS=

# Origem fb: Firebird legado. Opcional: sem estas chaves a origem não existe
# e o Hermes não tenta conectar.
SOURCE_FB_ADAPTER=firebird
SOURCE_FB_HOST=
SOURCE_FB_PORT=3050
SOURCE_FB_NAME=
SOURCE_FB_USER=
SOURCE_FB_PASS=
SOURCE_FB_CHARSET=ISO8859_1

# Destino
TARGET_ADAPTER=postgres
TARGET_HOST=target-db
TARGET_PORT=5432
TARGET_NAME=tenant
TARGET_USER=tenant
TARGET_PASS=tenant
```

Uma chave `SOURCE_` que não pertence a nenhuma origem declarada interrompe a execução antes de conectar. Isso pega erro de digitação, como `SOURCE_V2_HOST` sem `SOURCE_V2_ADAPTER`.

---

## Passo 3 — Criar `NomeAcme/NomeAcme.py`

Estrutura mínima:

```python
import os
import sys
import traceback

_clientdir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(_clientdir))

from core.base_migration import BaseMigration
from core.compatible import CopyEntity
from core.queries import NativeQuery, SourceQuery
from core.references import ReferenceMap
from core.table_migration import TableMigration
from core.strategies import Copy, Rename, Fixed, Lookup, RegexClean, DateConvert, EmailExtract, NullIfBlank, Truncate, Default


class Migracao(BaseMigration):

    def __init__(self):
        super().__init__(_clientdir)

    # ------------------------------------------------------------------
    # Hooks obrigatórios
    # ------------------------------------------------------------------

    def compatible_entities(self) -> list:
        # Tabelas que passam da V1 para a V2 sem transformação. Só o que está
        # aqui é copiado; uma tabela nova na V1 não entra sem ser listada.
        return [
            # CopyEntity('public.feriados', source='v1'),
            # CopyEntity('public.cidades_v1', source='v1', target='public.cidades'),
        ]

    def declare_reference_maps(self) -> list:
        # Mapas lidos da V2 antes da primeira fase. Use com
        # Lookup('f_campo', self.reference_map('nome_do_mapa')).
        return [
            # ReferenceMap('states_by_code', table='public.states', key='f_code', value='f_id'),
        ]

    def reset_sequences(self):
        # O adapter acha a sequence de cada coluna; não depende de nome.
        self.reset_identity([
            'public.acme_customers_demo',
            'public.acme_orders_demo',
            # adicione as demais tabelas com identidade
        ], column='f_id')

    # ------------------------------------------------------------------
    # Fase inicial (01_prepare_target)
    # ------------------------------------------------------------------

    def run_initial(self):
        self.comment('LIMPEZA DAS TABELAS DA V2')
        self.clear([
            'public.acme_orders_demo',
            'public.acme_customers_demo',
        ])
        self.comment('DESABILITAR TRIGGERS DE TABELAS')
        # Sem argumentos vale para todas as tabelas do destino. O runner
        # reabilita na fase final.
        self.suspend_constraints()

    # ------------------------------------------------------------------
    # Fase de carga (03_load_transformed_data)
    # ------------------------------------------------------------------

    def run_inserts(self):
        self.insert_tabela_a()
        self.insert_tabela_b()
        # adicione na ordem correta (respeite FKs)

    # ------------------------------------------------------------------
    # Métodos de inserção
    # ------------------------------------------------------------------

    def insert_tabela_a(self):
        TableMigration(
            source=SourceQuery('public.tabela_a', order_by='f_id'),
            target='public.tabela_a',
            fields=[
                Copy('f_id'),
                Copy('f_name'),
            ],
        ).run(self.context.source('v1'))

    def insert_tabela_b(self):
        # Junção não cabe num SourceQuery: a consulta vai inteira, presa ao adapter.
        TableMigration(
            source=NativeQuery('postgres', 'SELECT b.f_id, a.f_name FROM public.tabela_b b JOIN public.tabela_a a ON a.f_id = b.f_a'),
            target='public.tabela_b',
            fields=[
                Copy('f_id'),
                Copy('f_name'),
            ],
        ).run(self.context.source('v1'))


# ------------------------------------------------------------------
if __name__ == '__main__':
    try:
        m = Migracao()
        m.enable_debug(100)
        m.run()
    except Exception as e:
        print('[ERRO] ' + str(e))
        print(traceback.format_exc())
```

---

## Passo 4 — Identificar as tabelas

Para cada tabela da V1 que precisa ser migrada, decida a estratégia:

| Situação | Abordagem |
|---|---|
| Tabela idêntica em V1 e V2, talvez com outro nome | `CopyEntity` em `compatible_entities()`. Com `SOURCE_<NOME>_NATIVE_COPY=pg_dump`, as que não mudam de nome vão pelo `pg_dump` |
| Campos renomeados, FK diferente ou dados que precisam de limpeza | `TableMigration` com estratégias |
| Lógica condicional por linha ou múltiplas fontes | método manual que lê com `self.context.source(...).read()` e emite `Insert` |

Consulte [estrategias.md](estrategias.md) para o catálogo completo de estratégias.

---

## Passo 5 — Respeitar a ordem das inserções

`run_inserts()` deve inserir as tabelas de forma que nenhuma FK seja violada:

1. Tabelas de domínio (sem FKs): `acme_departments_demo`, `acme_statuses_demo`, etc.
2. Tabelas que dependem de domínio: `acme_customers_demo`, `acme_contracts_demo`.
3. Tabelas que dependem dessas entidades: `acme_orders_demo`, `acme_order_items_demo`, etc.
4. Tabelas dependentes das anteriores: `acme_notes_demo`, `acme_files_demo`, etc.

---

## Passo 6 — Testar em modo debug

```bash
python3 Migracao.py NomeAcme --debug 100
```

Isso limita cada consulta da origem a 100 linhas, inclusive as `NativeQuery`. Verifique:

- Nenhum erro de Python na saída do terminal.
- Os arquivos em `runs/NomeAcme/<timestamp>/comandos/` foram gerados.
- O SQL gerado parece correto (amostragem manual).

---

## Passo 7 — Executar os SQLs no banco V2

Veja o passo a passo completo em [execucao.md](execucao.md).

---

## Checklist resumido

- [ ] Diretório `NomeAcme/` criado com script e `.env`
- [ ] `.env` criado e configurado
- [ ] `.env.example` criado sem valores reais
- [ ] `NomeAcme/NomeAcme.py` criado herdando `BaseMigration`
- [ ] `compatible_entities()` com as tabelas que passam sem transformação
- [ ] `declare_reference_maps()` com os mapas lidos da V2, sem SQL solto no `run_inserts()`
- [ ] `reset_sequences()` com todas as tabelas que têm sequence
- [ ] `run_initial()` com `clear()` e `suspend_constraints()`
- [ ] `run_inserts()` com todos os métodos em ordem de FK
- [ ] Teste com `--debug 100` sem erros
- [ ] SQL gerado revisado
- [ ] Migração completa executada e logs verificados
