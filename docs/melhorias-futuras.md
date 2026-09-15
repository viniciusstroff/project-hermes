# Melhorias futuras

Este documento lista melhorias identificadas no sistema após a refatoração do Acme. O foco aqui não é “suportar qualquer banco imediatamente”, e sim deixar a arquitetura pronta para trocar ou adicionar novos adapters de origem e de destino sem reescrever o núcleo da migração nem os Acmes.

Também inclui um plano para desacoplar a forma de saída da migração. Hoje o sistema gera SQL em arquivos; futuramente a mesma migração deve poder:
- escrever em arquivos SQL
- aplicar direto no banco de destino
- emitir lotes para outro executor
- registrar artefatos intermediários para auditoria
- executar partes independentes em paralelo e com streaming
- retomar execuções a partir de checkpoints seguros
- emitir logs, métricas e relatórios operacionais
- acompanhar mudanças em tempo real quando houver captura de dados via Debezium

---

## Resumo

| # | Melhoria | Esforço | Impacto |
|---|---|---|---|
| 1 | Tornar a migração agnóstica de adapter de origem/destino | Alto | Muito alto |
| 2 | Tornar a saída configurável: migração direta vs gerar arquivos | Alto | Muito alto |
| 3 | Migrar outros Acmes para a nova arquitetura | Alto | Muito alto |
| 4 | Testes unitários e de contrato do core | Médio | Alto |
| 5 | Eliminar SQL manual residual dos Acmes | Médio | Alto |
| 6 | Expandir o uso de `MultiTargetMigration` | Médio | Médio |
| 7 | Padronizar lookups de referência | Médio | Médio |
| 8 | Revisar estratégias e expressões dependentes de dialeto | Médio | Médio |
| 9 | Limpar código legado e utilitários obsoletos | Baixo | Baixo |
| 10 | Criar interface gráfica para leigos/configuração assistida de migrações | Alto | Alto |
| 11 | Criar camada de execução performática para migrações pesadas, com opção de executor Go | Alto | Muito alto |
| 12 | Adicionar paralelismo e streaming controlados | Alto | Muito alto |
| 13 | Registrar checkpoints do estado atual da migração | Médio | Alto |
| 14 | Evoluir logs, métricas e relatórios de execução | Médio | Alto |
| 15 | Avaliar migração em tempo real com Debezium | Alto | Alto |

---

## 1 — Tornar a migração agnóstica de adapter de origem/destino

O projeto já separa o runtime de migração por adapters e providers, mas essa
camada ainda pode evoluir para novos bancos e novos tipos de saída:

- `DefaultMigrationContextFactory` ainda monta o contexto padrão com PostgreSQL e Firebird
- `BaseMigration` ainda expõe `pg_v1_conn`, `pg_v2_conn` e `fb_v1_conn` como compatibilidade transitória
- a saída padrão ainda é SQL PostgreSQL em arquivos
- novos bancos ainda exigem adapters, dialects e providers concretos

### Contrato de infraestrutura local

O ambiente Docker deve expor nomes estáveis e genéricos para o core de migração:

- rede Docker: `hermes-net`
- destino principal: `target-db`
- porta interna do destino: a porta nativa do adapter, por exemplo `5432` no PostgreSQL
- porta publicada no host: configurável via `TARGET_DB_PUBLISHED_PORT`

Assim, trocar o container de destino não exige alterar o container da aplicação. O contrato
do app continua sendo "conectar no host lógico do adapter"; o detalhe de imagem, volume e
porta publicada fica no compose específico do banco, como `docker-compose.postgres.yml`.

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

- adicionar adapters concretos para outros bancos quando houver demanda real
- remover atributos legados `pg_v1_conn`, `pg_v2_conn` e `fb_v1_conn` depois que os Acmes privados migrarem
- evoluir a saída para outros targets além de SQL PostgreSQL em arquivo
- manter novos Acmes usando adapters, providers e helpers do core em vez de cursores diretos

### Benefício real

Não significa “migrar para qualquer banco em um clique”. Significa que:
- trocar PostgreSQL V1 por outro source deixa de exigir refatoração geral
- trocar o destino por outro target viável passa a ser projeto de adapter, não reescrita do Acme
- Acme e os próximos Acmes deixam de misturar regra de negócio com detalhes do driver

---

## 2 — Tornar a saída da migração configurável: migração direta vs gerar arquivos

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

## 10 — Criar interface gráfica para leigos/configuração assistida de migrações

Hoje a configuração de uma migração exige conhecimento técnico do código, das tabelas de origem e destino, das estratégias de transformação e dos detalhes de execução. Futuramente, o projeto pode oferecer uma interface gráfica para que pessoas sem familiaridade com Python ou SQL consigam montar um plano de migração com apoio visual.

### Objetivo

Permitir que a migração seja configurada por meio de uma experiência guiada, reduzindo a dependência de edição manual de scripts para casos comuns.

### Funcionalidades esperadas

- selecionar conexões de origem e destino
- listar tabelas disponíveis na origem e no destino
- escolher quais tabelas serão migradas
- mapear origem => destino por tabela
- selecionar quais colunas entram na migração
- renomear colunas no destino
- configurar valores fixos, campos obrigatórios e valores padrão
- escolher estratégias de transformação já suportadas pelo core
- configurar lookups de referência
- validar incompatibilidades antes da execução
- salvar e reutilizar planos de migração
- gerar uma prévia do SQL ou das operações que seriam executadas

### Direção de design

A interface não deve escrever código Python diretamente. Ela deve produzir um plano declarativo de migração, por exemplo em YAML ou JSON, que depois seria interpretado pelo mesmo core usado pelos scripts.

Exemplo conceitual:

```yaml
tables:
  - source: customers
    target: public.people
    columns:
      - source: name
        target: f_name
      - source: document
        target: f_document
      - target: f_source
        fixed: migration
```

### Dependências arquiteturais

Essa melhoria fica mais viável depois de:

- estabilizar `SourceAdapter`, `TargetAdapter` e `OutputAdapter`
- padronizar introspecção de metadados por adapter
- transformar migrations comuns em configurações declarativas
- padronizar o catálogo de strategies disponíveis para a interface
- criar validação forte do plano antes da execução

### Benefício real

- reduz a barreira para configurar migrações simples e médias
- torna o mapeamento origem => destino mais auditável
- permite revisar visualmente tabelas, colunas e regras antes de executar
- facilita reaproveitar planos entre clientes parecidos
- mantém o core como fonte de verdade, evitando uma segunda lógica de migração na interface

---

## 11 — Criar camada de execução performática para migrações pesadas, com opção de executor Go

O Python continua adequado para orquestração, configuração, validação e organização das regras de migração. Porém, para volumes grandes, o gargalo pode deixar de ser a modelagem da migração e passar a ser a execução pesada: leitura em massa, transformação, escrita no destino, controle de transação, uso de memória e tempo total de carga.

### Objetivo

Separar o plano e a orquestração da migração da camada que executa o movimento pesado dos dados.

### Direção de design

Criar uma abstração de execução:

```python
class ExecutionAdapter(Protocol):
    def validate_plan(self, plan): ...
    def dry_run(self, plan): ...
    def execute(self, plan): ...
    def progress(self, execution_id: str): ...
```

Essa camada recebe um plano de migração já validado e decide a melhor estratégia de execução para cada cenário.

### Estratégias de execução possíveis

- `sql-file`: comportamento atual, gerando arquivos SQL auditáveis
- `direct-db`: execução direta no banco de destino
- `bulk-copy`: uso de carga em massa, como `COPY`, staging tables e batches grandes
- `db-native`: mover o máximo possível para SQL set-based no banco
- `duckdb-polars`: usar ferramentas colunares para transformação local antes da carga
- `external-worker`: delegar execução pesada para um worker em outra linguagem

### Possível arquitetura

```text
Interface grafica / API Python
  -> monta e valida migration_plan.json
  -> registra execucao e artefatos
  -> chama ExecutionAdapter

ExecutionAdapter
  -> escolhe sql-file, direct-db, bulk-copy ou external-worker
  -> emite logs, progresso e metricas
  -> retorna manifesto da execucao

Executor externo opcional
  -> Rust ou Go para streaming, transformacao e carga massiva
```

### Quando considerar outra linguagem

Outra linguagem só deve entrar depois de medir onde está o gargalo. Antes disso, é mais importante eliminar execução linha a linha e usar recursos nativos de banco:

- `COPY` ou mecanismo equivalente para carga em massa
- staging tables para validação e transformação intermediária
- inserts em lote
- transações controladas por etapa
- desativação/recriação controlada de índices e triggers quando seguro
- transformações set-based no banco de destino

Se, mesmo depois disso, o gargalo continuar em CPU, parsing, transformação linha a linha, normalização massiva ou streaming entre fontes heterogêneas, pode fazer sentido criar um `hermes-executor` separado.

### Linguagens candidatas para executor externo

- Rust: melhor escolha quando a prioridade for performance, baixo uso de memória, binário único e streaming robusto.
- Go: boa escolha quando a prioridade for simplicidade operacional, concorrência e manutenção por uma equipe maior.

O ponto principal é manter o contrato em arquivo ou API, como JSON/YAML, para que o core Python não dependa dos detalhes internos do executor.

### Direção inicial para Go

Se a equipe priorizar Go, o executor pode começar pequeno e bem delimitado:

- receber um `migration_plan.json` validado pelo Python
- ler dados em batches ou cursores de streaming
- aplicar transformações simples e determinísticas
- escrever no destino usando bulk insert, `COPY` ou mecanismo equivalente do adapter
- expor progresso por stdout estruturado, arquivo de estado ou endpoint local
- devolver um manifesto final com linhas lidas, escritas, rejeitadas e tempo por etapa

O Go não deve virar a camada de regra da migração. Ele deve executar trabalho pesado definido por um plano já validado.

### Benefício real

- permite manter Python onde ele é mais produtivo
- evita acoplar interface, regra e execução pesada no mesmo processo
- cria espaço para otimizações específicas por banco
- facilita medir e comparar estratégias de carga
- permite evoluir para Rust ou Go sem reescrever a camada de configuração

---

## 12 — Adicionar paralelismo e streaming controlados

Hoje a migração é pensada como uma sequência previsível de etapas. Isso favorece rastreabilidade, mas limita desempenho em bases grandes. Futuramente, o motor pode executar partes independentes em paralelo e processar dados em streaming, sem carregar tudo em memória.

### Objetivo

Reduzir tempo total e uso de memória em migrações grandes, mantendo ordem, dependências e auditabilidade explícitas.

### Direção de design

Representar a execução como um plano com dependências:

```text
prepare_target
  -> migrate_reference_tables
  -> migrate_independent_tables em paralelo
  -> migrate_dependent_tables
  -> finalize_target
```

Cada unidade de trabalho deve declarar:

- tabelas ou queries de origem
- tabelas de destino afetadas
- dependências obrigatórias
- tamanho de batch
- política de transação
- se aceita paralelismo por tabela, por partição ou por intervalo de chave

### Streaming

O streaming deve ser tratado como contrato do `SourceAdapter` e do `ExecutionAdapter`:

- leitura por cursor ou paginação estável
- batches com tamanho configurável
- backpressure quando o destino estiver mais lento que a origem
- serialização incremental para arquivo ou envio direto ao destino
- contadores por batch para progresso e checkpoint

### Cuidados

- respeitar FKs, lookups e dependências entre tabelas
- não paralelizar etapas que compartilham estado mutável sem controle
- garantir idempotência ou checkpoints antes de retomar uma etapa
- preservar uma forma de reproduzir ou auditar a execução

---

## 13 — Registrar checkpoints do estado atual da migração

Uma migração longa não deve precisar começar do zero depois de uma falha operacional. O projeto pode evoluir para registrar checkpoints confiáveis por fase, tabela e batch.

### Objetivo

Permitir retomada segura, diagnóstico de falhas e execução incremental controlada.

### Informações mínimas do checkpoint

- `execution_id`
- Acme/plano executado
- fase atual
- tabela ou unidade de trabalho atual
- último batch concluído
- quantidade de linhas lidas, escritas e rejeitadas
- hash ou versão do plano usado
- adapters e configuração relevante
- status: `pending`, `running`, `done`, `failed`, `skipped`

### Possíveis armazenamentos

- arquivo local `state/checkpoint.json` dentro do diretório de artefatos
- tabela de controle no banco de destino
- storage externo, caso a execução rode em worker separado

### Regras importantes

- checkpoint só deve avançar depois de uma unidade de trabalho confirmada
- retomada deve validar que o plano e os adapters continuam compatíveis
- etapas não idempotentes precisam de estratégia explícita de compensação ou limpeza
- o manifesto final deve apontar todos os checkpoints usados na execução

---

## 14 — Evoluir logs, métricas e relatórios de execução

O projeto já gera `logs/migration.log` e `manifest.json`, mas pode evoluir para observabilidade operacional completa.

### Objetivo

Dar visibilidade objetiva sobre duração, volume, gargalos, falhas e qualidade da migração.

### Logs

- logs estruturados em JSON Lines, além do log textual quando útil
- correlação por `execution_id`, fase, tabela e batch
- registro de erros com contexto suficiente para reprocessar ou corrigir dados
- níveis claros: `debug`, `info`, `warning`, `error`

### Métricas

- linhas lidas, transformadas, escritas, ignoradas e rejeitadas
- tempo por fase, tabela e batch
- throughput por tabela
- consumo de memória quando disponível
- tamanho dos artefatos gerados
- quantidade de retries e falhas por tipo

### Relatórios

- resumo executivo da execução
- relatório de divergências e registros rejeitados
- relatório por tabela com contagens e tempo
- comparação entre origem e destino quando houver regra de validação
- exportação em Markdown, HTML ou JSON para auditoria

---

## 15 — Avaliar migração em tempo real com Debezium

Além da migração em lote, alguns cenários podem exigir replicar alterações feitas na origem enquanto a migração principal acontece. Nesses casos, Debezium pode ser avaliado como camada de Change Data Capture.

### Objetivo

Permitir um fluxo em duas etapas:

1. carga inicial dos dados históricos
2. captura e aplicação contínua das mudanças ocorridas depois do início da carga

### Arquitetura conceitual

```text
Banco origem
  -> Debezium
  -> Kafka ou outro broker compatível
  -> consumidor Hermes CDC
  -> adapters/outputs do destino
```

### Pontos de atenção

- Debezium depende do suporte de CDC/log do banco de origem
- transformações precisam ser compatíveis com eventos de insert, update e delete
- ordenação, deduplicação e idempotência viram requisitos centrais
- é necessário definir o ponto de corte entre carga inicial e streaming
- o destino precisa lidar com upsert, conflitos e exclusões de forma consistente
- observabilidade e checkpoints são obrigatórios para não perder eventos

### Quando faz sentido

- janelas de parada muito pequenas
- bases grandes com alta atividade durante a migração
- necessidade de manter destino quase sincronizado antes do cutover
- projetos com infraestrutura para operar Debezium, Kafka/broker e consumidores

---

## Ordem recomendada

Para evoluir com segurança, a ordem sugerida é:

1. Introduzir contratos de `SourceAdapter`, `TargetAdapter` e `OutputAdapter`
2. Adaptar `BaseMigration` para depender desses contratos sem mudar o comportamento externo
3. Manter `SqlFileOutput` como padrão e extrair o buffer atual para ele
4. Migrar Acmes restantes para o `core/`
5. Reduzir SQL manual residual
6. Adicionar novo output (`MirrorOutput` ou `DirectDatabaseOutput`)
7. Definir um formato declarativo para planos de migração
8. Criar `ExecutionAdapter` para separar orquestração de execução pesada
9. Adicionar logs estruturados, métricas e relatórios
10. Adicionar checkpoints por fase/tabela/batch
11. Implementar um executor `bulk-copy` antes de considerar outra linguagem
12. Introduzir paralelismo e streaming apenas depois de checkpoints e métricas
13. Só depois considerar novos adapters concretos de origem/destino
14. Implementar a interface gráfica usando o plano declarativo como contrato
15. Avaliar um executor externo em Go ou Rust apenas se medições reais justificarem
16. Avaliar Debezium para migração em tempo real quando houver necessidade real de CDC

---

## Resultado esperado

Ao final desse plano, a migração deve continuar funcionando como hoje, mas com as seguintes propriedades:

- origem e destino deixam de ser detalhes embutidos no Acme
- adicionar ou trocar adapters vira trabalho localizado
- a forma de saída deixa de ser fixa
- o núcleo da migração fica mais testável
- Acme e outros Acmes passam a declarar regra de migração, não infraestrutura de execução
- usuários menos técnicos conseguem configurar migrações comuns com uma interface guiada
- migrações grandes podem usar estratégias de execução mais rápidas sem trocar a camada de configuração
- execuções longas podem ser monitoradas, auditadas e retomadas com checkpoints
- cenários com janela curta de parada podem avaliar CDC com Debezium
