# Criando a migração de um novo Acme

Este documento descreve o que é necessário para adicionar um novo escritório ao sistema. O objetivo é ter um checklist que pode ser seguido do zero até a primeira execução.

---

## Estrutura esperada

```
Migracao/
└── NomeAcme/
    ├── NomeAcme.py   ← script de migração
    ├── .env             ← credenciais e caminhos (nunca comitar)
    ├── .env.example     ← versão sem valores reais (comitar)
    ├── sql/             ← arquivos SQL gerados (ignorados pelo git)
    ├── logs/            ← logs de execução psql (ignorados pelo git)
    └── erros/           ← erros filtrados dos logs (ignorados pelo git)
```

O nome do diretório e do arquivo `.py` devem ser idênticos (case-sensitive). Esse nome é passado como argumento para `python3 Migracao.py <NomeAcme>`.

---

## Passo 1 — Criar o diretório e os subdiretórios

```bash
mkdir -p NomeAcme/sql NomeAcme/logs NomeAcme/erros
```

---

## Passo 2 — Criar o `.env`

Copie de um Acme existente e ajuste:

```bash
cp Acme/.env.example NomeAcme/.env
```

Variáveis obrigatórias:

```dotenv
# Base legada PostgreSQL V1
PG_V1_HOST=
PG_V1_PORT=
PG_V1_NAME=
PG_V1_USER=
PG_V1_PASS=

# Base destino PostgreSQL V2
PG_V2_HOST=
PG_V2_PORT=
PG_V2_NAME=
PG_V2_USER=
PG_V2_PASS=

# Firebird V1 (se não usar, manter preenchido com valores fictícios — a conexão é sempre tentada)
FB_V1_HOST=
FB_V1_PORT=3050
FB_V1_NAME=
FB_V1_USER=
FB_V1_PASS=
FB_V1_CHARSET=ISO8859_1

# Arquivos de saída
EMPTY_FILENAME=sql/empty.sql
DUMP_FILENAME=sql/webstagepgj_cmd.dmp.sql
FINAL_FILENAME=sql/cmd_fim.sql
INICIAL_FILENAME=sql/cmd_ini.sql
PRINCIPAL_FILENAME=sql/cmd.sql
```

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
from core.table_migration import TableMigration
from core.strategies import Copy, Rename, Fixed, Lookup, RegexClean, DateConvert, EmailExtract, SqlExpression


class Migracao(BaseMigration):

    def __init__(self):
        super().__init__(_clientdir)

    # ------------------------------------------------------------------
    # Hooks obrigatórios
    # ------------------------------------------------------------------

    def dump_ignoring_tables(self) -> list:
        return [
            # tabelas que existem na V1 mas não devem ir para a V2
            # 'public.tabela_obsoleta',
        ]

    def dump_non_existing_tables(self) -> list:
        return [
            # tabelas referenciadas no schema mas ausentes no Acme
            # 'public.tabela_que_nao_existe_aqui',
        ]

    def set_indexes_commands(self):
        # Desabilitar índices que tornam a carga muito lenta.
        # Deixe vazio se não houver índices problemáticos.
        pass

    def reset_sequences(self):
        tables = [
            'process',
            'clients',
            # adicione as demais tabelas com sequences
        ]
        for table in tables:
            self.write_sql(
                f"SELECT pg_catalog.setval('{table}_f_id_seq', "
                f"(SELECT MAX(f_id) FROM public.{table}), true);\n"
            )

    # ------------------------------------------------------------------
    # Fase inicial (cmd_ini.sql)
    # ------------------------------------------------------------------

    def run_initial(self):
        self.create_truncates()
        self.print_comment('DESABILITAR TRIGGERS DE TABELAS')
        self.write_sql(self.disable_triggers)
        self.print_comment('DESABILITAR ÍNDICES DE TABELAS')
        self.write_sql(self.disable_indexes)

    # ------------------------------------------------------------------
    # Fase de carga (cmd.sql)
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
            source_sql='SELECT {fields} FROM public.tabela_a {limit}',
            target='public.tabela_a',
            fields=[
                Copy('f_id'),
                Copy('f_name'),
            ],
        ).run(self._engine)


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
| Tabela idêntica ou quase idêntica em V1 e V2 | `pg_dump` (lista em `dump_ignoring_tables` apenas as que *não* devem ser incluídas) |
| Campos renomeados, FK diferente ou dados que precisam de limpeza | `TableMigration` com estratégias |
| Lógica condicional por linha ou múltiplas fontes | método manual com `write_sql()` |

Consulte [estrategias.md](estrategias.md) para o catálogo completo de estratégias.

---

## Passo 5 — Respeitar a ordem das inserções

`run_inserts()` deve inserir as tabelas de forma que nenhuma FK seja violada:

1. Tabelas de domínio (sem FKs): `users`, `hearingtypes`, `actiontypes`, etc.
2. Tabelas que dependem de domínio: `clients`, `process`.
3. Tabelas que dependem de `process` e `clients`: `participants`, `publications`, etc.
4. Tabelas dependentes das anteriores: `histories`, `documents`, etc.

---

## Passo 6 — Testar em modo debug

```bash
python3 Migracao.py NomeAcme --debug 100
```

Isso limita cada `SELECT` a 100 linhas. Verifique:

- Nenhum erro de Python na saída do terminal.
- Os arquivos em `NomeAcme/sql/` foram gerados.
- O SQL gerado parece correto (amostragem manual).

---

## Passo 7 — Executar os SQLs no banco V2

Veja o passo a passo completo em [execucao.md](execucao.md).

---

## Checklist resumido

- [ ] Diretório `NomeAcme/` criado com `sql/`, `logs/` e `erros/`
- [ ] `.env` criado e configurado
- [ ] `.env.example` criado sem valores reais
- [ ] `NomeAcme/NomeAcme.py` criado herdando `BaseMigration`
- [ ] `dump_ignoring_tables()` revisado
- [ ] `dump_non_existing_tables()` revisado
- [ ] `reset_sequences()` com todas as tabelas que têm sequence
- [ ] `run_initial()` com truncates e disables
- [ ] `run_inserts()` com todos os métodos em ordem de FK
- [ ] Teste com `--debug 100` sem erros
- [ ] SQL gerado revisado
- [ ] Migração completa executada e logs verificados
