# Melhorias futuras

Este documento lista melhorias identificadas no sistema após a refatoração do Acme. O foco aqui não é “suportar qualquer banco imediatamente”, e sim deixar a arquitetura pronta para trocar ou adicionar novos adapters de origem e de destino sem reescrever o núcleo da migração nem os Acmes.

Também inclui um plano para desacoplar a forma de saída da migração. Hoje o sistema gera SQL em arquivos; futuramente a mesma migração deve poder:
- escrever em arquivos SQL
- aplicar direto no banco de destino
- emitir lotes para outro executor
- registrar artefatos intermediários para auditoria

---

## Resumo

| # | Melhoria | Esforço | Impacto |
|---|---|---|---|
| 1 | Tornar a migração agnóstica de adapter de origem/destino | Alto | Muito alto |
| 2 | Tornar a saída da migração configurável | Alto | Muito alto |
| 3 | Migrar outros Acmes para a nova arquitetura | Alto | Muito alto |
| 4 | Testes unitários e de contrato do core | Médio | Alto |
| 5 | Eliminar SQL manual residual dos Acmes | Médio | Alto |
| 6 | Expandir o uso de `MultiTargetMigration` | Médio | Médio |
| 7 | Padronizar lookups de referência | Médio | Médio |
| 8 | Revisar estratégias e expressões dependentes de dialeto | Médio | Médio |
| 9 | Limpar código legado e utilitários obsoletos | Baixo | Baixo |

---

## 1 — Tornar a migração agnóstica de adapter de origem/destino

Hoje o projeto já tem uma boa separação entre Acme e core, mas ainda existe acoplamento forte com PostgreSQL e Firebird:

- `BaseMigration` abre conexões concretas de `psycopg2` e `firebirdsql`
- `MigrationEngine` conhece detalhes de cursor de PostgreSQL e Firebird
- o Acme ainda acessa `pg_v1_conn`, `pg_v2_conn` e `fb_v1_conn` diretamente em alguns pontos
- a serialização SQL de saída assume PostgreSQL
- manutenção de triggers, índices e sequences está toda acoplada a PostgreSQL

### Objetivo

Permitir que o núcleo da migração trabalhe com contratos abstratos, e que PostgreSQL/Firebird sejam apenas implementações concretas desses contratos.

### Direção de design

Criar quatro camadas explícitas:

1. `SourceAdapter`
   Responsável por leitura de dados da origem.
   Ex.: PostgreSQL V1, Firebird V1, outro banco legado.

2. `TargetAdapter`
   Responsável por representar o destino.
   Ex.: PostgreSQL V2 hoje; no futuro, outro banco ou outro tipo de sink.

3. `Dialect`
   Responsável por diferenças de sintaxe e serialização.
   Ex.: `LIMIT`, escape de string, quoting de identificadores, sequences, funções SQL específicas.

4. `MetadataProvider`
   Responsável por operações auxiliares do banco:
   - listar tabelas
   - gerar comandos de trigger/index
   - resolver referências
   - sequences/reset

### Possível estrutura

```text
core/
  adapters/
    source_adapter.py
    target_adapter.py
    postgres_source.py
    firebird_source.py
    postgres_target.py
  dialects/
    base.py
    postgres.py
    firebird.py
  metadata/
    base.py
    postgres.py
  outputs/
    base.py
    sql_file_output.py
    direct_db_output.py
  migrations/
    table_migration.py
    expand_migration.py
    multi_target_migration.py
```

### Contratos sugeridos

```python
class SourceAdapter(Protocol):
    def cursor(self): ...
    def scalar(self, sql: str): ...
    def limit_clause(self, limit: str) -> str: ...

class TargetAdapter(Protocol):
    def serialize_value(self, value) -> str: ...
    def insert_statement(self, table: str, columns: list[str], values: list[str]) -> str: ...

class MetadataProvider(Protocol):
    def load_reference_map(self, table: str, key_col: str, value_col: str, **kwargs) -> dict: ...
    def disable_triggers_sql(self) -> str: ...
    def enable_triggers_sql(self) -> str: ...
    def reset_sequence_sql(self, table: str) -> str: ...
```

### O que precisa mudar

- `BaseMigration` deixa de abrir conexões concretas diretamente
- `MigrationEngine` passa a depender de `SourceAdapter`
- `sql_value()` e geração de `INSERT` passam a depender de `TargetAdapter` ou `TargetDialect`
- `set_trigger_commands()` e `reset_sequences()` deixam de falar com PostgreSQL diretamente
- Acmes deixam de acessar `pg_v1_conn.cursor()` e similares; usam adapters ou helpers do core

### Benefício real

Não significa “migrar para qualquer banco em um clique”. Significa que:
- trocar PostgreSQL V1 por outro source deixa de exigir refatoração geral
- trocar o destino por outro target viável passa a ser projeto de adapter, não reescrita do Acme
- Acme e os próximos Acmes deixam de misturar regra de negócio com detalhes do driver

---

## 2 — Tornar a saída da migração configurável

Hoje a migração escreve SQL em arquivos e esse comportamento está embutido no fluxo principal. Isso funciona bem para auditoria e reexecução parcial, mas não deveria ser a única forma de entrega.

### Objetivo

Separar “gerar comandos” de “como entregar/executar esses comandos”.

### Cenários desejados

- `sql-file`: escreve `01_prepare_target.sql`, `03_load_transformed_data.sql`, `04_finalize_target.sql`
- `direct-db`: executa diretamente no destino
- `buffered-db`: executa em lotes no destino
- `stdout`: emite comandos para pipe/processamento externo
- `hybrid`: escreve arquivo e também aplica no banco
- `artifact-bundle`: escreve SQL + logs + manifesto da execução

### Direção de design

Criar uma abstração de saída:

```python
class OutputAdapter(Protocol):
    def open_phase(self, phase: str): ...
    def write(self, sql: str): ...
    def flush(self): ...
    def close(self): ...
```

### Implementações possíveis

#### `SqlFileOutput`

Comportamento atual:
- abre arquivos por fase
- acumula buffer
- flush em disco

#### `DirectDatabaseOutput`

Executa direto no destino:
- recebe SQL ou operação estruturada
- controla transações
- pode fazer commit por lote
- exige estratégia de rollback e observabilidade

#### `MirrorOutput`

Encaminha para mais de um destino:
- arquivo + banco
- arquivo + stdout

#### `ManifestOutput`

Além dos comandos, gera um manifesto:
- Acme
- data/hora
- adapters usados
- quantidade de linhas por step
- hash dos artefatos

### O que precisa mudar

- `BaseMigration.write_sql()` deixa de escrever em arquivo diretamente
- `BaseMigration` recebe um `OutputAdapter`
- o buffer atual passa a ser responsabilidade do output, não da migração
- o `run()` continua definindo fases, mas delega a abertura/troca de artefato para o output

### Possível configuração futura

```python
MigrationRunner(
    source_main=PostgresSourceAdapter(...),
    source_aux=FirebirdSourceAdapter(...),
    target=PostgresTargetAdapter(...),
    output=SqlFileOutput(...),
).run(client_plan)
```

ou:

```bash
python3 Migracao.py Acme --output sql-file
python3 Migracao.py Acme --output direct-db
python3 Migracao.py Acme --output hybrid
```

### Riscos

- execução direta aumenta o impacto de erros lógicos
- perde-se parte da auditabilidade se não houver manifesto/log forte
- algumas operações hoje pensadas como texto SQL talvez precisem virar operações estruturadas no futuro

### Estratégia recomendada

Fase 1:
- manter SQL em arquivo como padrão
- introduzir `OutputAdapter` sem mudar o comportamento externo

Fase 2:
- adicionar `MirrorOutput`
- adicionar `DirectDatabaseOutput` para ambientes controlados

---

## 3 — Migrar os outros clientes fictícios para a nova arquitetura

Os clientes fictícios `AcmeAlpha`, `AcmeBeta`, `AcmeGamma` e `AcmeDelta` ainda têm seus próprios `print_log`, `write_sql`, `set_trigger_commands` e buffers. Qualquer melhoria feita no `core/` não chega automaticamente neles.

**O que fazer para cada Acme:**
- herdar `BaseMigration`
- remover infraestrutura duplicada
- converter métodos `insert_*` simples para `TableMigration`, `ExpandMigration` ou `MultiTargetMigration`
- substituir acesso direto a conexões por helpers/adapters do core
- seguir o checklist em [novo-Acme.md](novo-Acme.md)

**Arquivos:** `AcmeAlpha/Migracao.py`, `AcmeBeta/Migracao.py`, `AcmeGamma/Migracao.py`, `AcmeDelta/Migracao.py`

---

## 4 — Testes unitários e de contrato do core

Agora que o `core/` já tem mais abstrações (`TableMigration`, `ExpandMigration`, `MultiTargetMigration`, strategies), o próximo passo é garantir contratos estáveis.

### O que cobrir

- strategies: valor normal, `None`, string vazia, aspas, barra invertida
- `Lookup`: fallback, normalização de chave, referências ausentes
- `Transform`: callback simples e renomeação
- `TableMigration`: seleção de colunas, progress, validação de strategies
- `ExpandMigration`: fan-out
- `MultiTargetMigration`: múltiplos targets, condições por target
- adapters futuros: mesma suíte para cada implementação

### Evolução esperada

Separar os testes em:
- `tests/test_strategies.py`
- `tests/test_table_migration.py`
- `tests/test_expand_migration.py`
- `tests/test_multi_target_migration.py`
- `tests/contracts/` para adapters e outputs

---

## 5 — Eliminar SQL manual residual dos Acmes

O Acme melhorou bastante, mas ainda há métodos montando SQL manualmente. Esses pontos continuam frágeis:

- escaping inconsistente
- dificuldade de testar
- acoplamento ao dialeto
- risco de divergência em relação ao resto do core

### Próximos candidatos a refatoração

- `insert_groups`
- `insert_actionobjects`
- `insert_annotations`
- `insert_depositvalues_types`
- `insert_appointmenttypes`

### Objetivo

Deixar o Acme o mais declarativo possível, e empurrar o comportamento genérico para:
- strategies
- helpers de referência
- migrations do core
- adapters de output/source/target

---

## 6 — Expandir o uso de `MultiTargetMigration`

`MultiTargetMigration` já cobre o padrão “uma linha da V1 gera inserts em múltiplas tabelas da V2”. Hoje isso já beneficia exemplos fictícios como `acme_messages_demo` e `acme_payments_demo`, mas a abstração ainda pode crescer.

### Possíveis evoluções

- permitir `before_row()` e `after_row()` para casos especiais
- permitir targets opcionais com predicates mais ricos
- permitir “shared computed fields” por linha
- permitir escrever operações não apenas como SQL, mas como operação estruturada para outputs diretos

### Quando usar

- tabela principal + tabelas filhas
- split de colunas por responsabilidade de tabela
- fan-out heterogêneo por linha

---

## 7 — Padronizar lookups de referência

O padrão de `states_map` já foi melhorado, mas ele ainda pode virar uma API mais declarativa.

### Objetivo

Evitar SQL auxiliar solto em Acmes para construir mapas de referência.

### Possível API

```python
self.reference_map(
    name='states_by_code',
    table='public.states',
    key='f_code',
    value='f_id',
    key_normalizer=lambda v: str(v).strip().upper(),
)
```

### Benefícios

- cache centralizado
- menos SQL repetido
- menor acoplamento com o banco alvo
- mesma interface para qualquer `MetadataProvider`

---

## 8 — Revisar estratégias e expressões dependentes de dialeto

Algumas strategies ainda pressupõem detalhes do banco:
- `SqlExpression`
- expressões como `regexp_replace(...)`
- casts e funções do PostgreSQL

### Objetivo

Preparar a arquitetura para que:
- o caminho principal seja Python e agnóstico
- expressões SQL específicas fiquem explícitas como recurso avançado

### Alternativas possíveis

- manter `SqlExpression`, mas renomear conceitualmente para algo como “dialect-specific”
- criar strategies portáveis equivalentes:
  - `NullIfBlank`
  - `Substring`
  - `Upper`
  - `RegexReplace`
  - `Coalesce`

---

## 9 — Limpar código legado e utilitários obsoletos

Há pontos pequenos que não mudam a arquitetura, mas reduzem ruído:

- revisar `common/dateutil.py`
- revisar imports antigos em `core/engine.py` e `core/__init__.py`
- consolidar documentação já implementada em relação ao que ainda é futuro
- remover helpers locais que ficaram duplicados após a refatoração

---

## Ordem recomendada

Para evoluir com segurança, a ordem sugerida é:

1. Introduzir contratos de `SourceAdapter`, `TargetAdapter` e `OutputAdapter`
2. Adaptar `BaseMigration` para depender desses contratos sem mudar o comportamento externo
3. Manter `SqlFileOutput` como padrão e extrair o buffer atual para ele
4. Migrar Acmes restantes para o `core/`
5. Reduzir SQL manual residual
6. Adicionar novo output (`MirrorOutput` ou `DirectDatabaseOutput`)
7. Só depois considerar novos adapters concretos de origem/destino

---

## Resultado esperado

Ao final desse plano, a migração deve continuar funcionando como hoje, mas com as seguintes propriedades:

- origem e destino deixam de ser detalhes embutidos no Acme
- adicionar ou trocar adapters vira trabalho localizado
- a forma de saída deixa de ser fixa
- o núcleo da migração fica mais testável
- Acme e outros Acmes passam a declarar regra de migração, não infraestrutura de execução
