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
| 1 | Tornar a migração agnóstica de adapter de origem/destino (feito) | Alto | Muito alto |
| 2 | Tornar a saída configurável: migração direta vs gerar arquivos (feito) | Alto | Muito alto |
| 3 | Migrar outros Acmes para a nova arquitetura | Alto | Muito alto |
| 4 | Testes unitários e de contrato do core | Médio | Alto |
| 5 | Eliminar SQL manual residual dos Acmes | Médio | Alto |
| 6 | Expandir o uso de `MultiTargetMigration` | Médio | Médio |
| 7 | Padronizar lookups de referência (feito) | Médio | Médio |
| 8 | Revisar estratégias e expressões dependentes de dialeto (feito) | Médio | Médio |
| 9 | Limpar código legado e utilitários obsoletos (feito) | Baixo | Baixo |
| 10 | Criar interface gráfica para leigos/configuração assistida de migrações | Alto | Alto |
| 11 | Criar camada de execução performática para migrações pesadas, com opção de executor Go | Alto | Muito alto |
| 12 | Adicionar paralelismo e streaming controlados | Alto | Muito alto |
| 13 | Registrar checkpoints do estado atual da migração | Médio | Alto |
| 14 | Evoluir logs, métricas e relatórios de execução (primeira parte feita) | Médio | Alto |
| 15 | Avaliar migração em tempo real com Debezium | Alto | Alto |

---

## 1 — Tornar a migração agnóstica de adapter de origem/destino

Feito pela change `desacoplar-migracao-de-banco-e-versao`. O core não depende mais de PostgreSQL, de Firebird nem de uma versão fixa:

- conexões nomeadas no `.env` (`SOURCE_<NOME>_*` e `TARGET_*`) e registro de adapters (`postgres`, `firebird`, `mongodb` ou `pacote.modulo:Classe`), com o driver importado só ao conectar;
- leitura por `SourceQuery`, `NativeQuery` e `NativeField`;
- escrita por operações (`Insert`, `Clear`, `SuspendConstraints`, `ResumeConstraints`, `ResetIdentity`, `Native` e `Comment`) traduzidas pelo adapter de destino;
- artefatos no formato do destino em `comandos/` (`.sql` ou `.jsonl`), listados no manifesto com a versão de cada servidor;
- cópia das entidades compatíveis por lista de inclusão, com o `pg_dump` como atalho opcional e versão conferida;
- aplicação pela sessão do adapter, guiada pelas capacidades (transação e savepoint);
- faixa de versão declarada por adapter e conferida ao conectar;
- `Dockerfile` sem cliente PostgreSQL fixo;
- adapter MongoDB nos dois lados.

A AcmeEdu, sem SQL próprio, passou numa matriz de seis caminhos: PostgreSQL 9.6 para 16, 17 para 17, PostgreSQL para MongoDB, MongoDB para PostgreSQL e Firebird 3 e 5 para PostgreSQL. O guia para converter os clientes privados está em [conversao-clientes.md](conversao-clientes.md).

### Contrato de infraestrutura local

O ambiente Docker deve expor nomes estáveis e genéricos para o core de migração:

- rede Docker: `hermes-net`
- destino principal: `target-db`
- porta interna do destino: a porta nativa do adapter, por exemplo `5432` no PostgreSQL
- porta publicada no host: configurável via `TARGET_DB_PUBLISHED_PORT`

Assim, trocar o container de destino não exige alterar o container da aplicação. O contrato
do app continua sendo "conectar no host lógico do adapter"; o detalhe de imagem, volume e
porta publicada fica no compose específico do banco, como `docker-compose.postgres.yml`.

### O que ficou de fora

- adapters para outros bancos, quando houver demanda real; cada um é um módulo com o contrato do papel, descrito em [arquitetura.md](arquitetura.md#contratos-por-papel);
- um segundo destino relacional (MySQL ou SQL Server) para provar que os contratos não ficaram com cara de PostgreSQL, deixado em aberto no design da change;
- aplicação sem transação num MongoDB fora de replica set, também em aberto;
- o output direto no banco, que é o item 2.

### Benefício real

Não significa "migrar para qualquer banco em um clique". Significa que:
- trocar o banco da V1 deixa de exigir refatoração geral, só um `.env` e, no máximo, as `NativeQuery` do cliente
- trocar o destino passa a ser projeto de adapter, não reescrita do cliente
- os clientes deixam de misturar regra de negócio com detalhes do driver

---

## 2 — Tornar a saída da migração configurável: migração direta vs gerar arquivos

A migração escreve SQL em arquivos. Isso funciona bem para auditoria e reexecução parcial, mas não deveria ser a única forma de entrega.

### Situação atual

O output adapter v1 foi implementado pela change `introduzir-output-adapter-sql-file`. Ela criou o contrato `OutputAdapter` em `core/outputs/` e transformou o antigo `SqlArtifactWriter` no adapter padrão `SqlFileOutput`. `MigrationContext`, `MigrationRunner` e `BaseMigration` passaram a escrever por `context.output` sem mudar os arquivos gerados nem o manifesto. Como o projeto não está em produção, `context.writer` e `SqlArtifactWriter` foram removidos em vez de virarem alias. Os detalhes estão em [arquitetura.md](arquitetura.md#saída-da-migração).

A change `introduzir-mirror-output` fez a Fase 2. O contrato ganhou `finish(success)`, chamado pelo `MigrationRunner` uma vez por execução, e `describe()`, cuja saída o manifesto grava na chave `output`. O `MirrorOutput` entrega os mesmos comandos para mais de um output.

A change `aplicar-artefatos-sql-no-destino` entregou a transação única e o dry run sem criar um output novo. O `SqlArtifactApplier`, em `core/apply/`, aplica os arquivos de uma execução depois da geração, com os modos `apply` e `dry-run` e as políticas de erro `stop` e `continue`. Ele roda pelo `Aplicar.py` ou pelo `Migracao.py` com `--apply` ou `--dry-run`. O uso está em [execucao.md](execucao.md#aplicando-os-sqls-no-destino) e o desenho em [arquitetura.md](arquitetura.md#aplicação-no-destino).

O `DirectDatabaseOutput` ficou adiado, pelo motivo explicado mais abaixo. Ainda não existe forma de escolher o output pelo CLI ou pelo `.env`.

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

A abstração de saída atual é:

```python
class OutputAdapter(Protocol):
    def open_phase(self, phase: str): ...
    def write(self, sql: str): ...
    def comment(self, text: str): ...
    def flush(self): ...
    def finish(self, success: bool): ...
    def describe(self) -> dict: ...
    def close(self): ...
```

### Implementações possíveis

#### `FileOutput` (implementado)

Sucessor do `SqlFileOutput`, em `core/outputs/file.py`:
- recebe operações e pede ao adapter de destino os comandos de cada uma
- abre um arquivo por fase em `comandos/`, com a extensão do formato do adapter, pelo `ArtifactManager.command_path()`
- acumula até 500 comandos em buffer
- grava o buffer ao trocar de fase, em `flush()` e em `close()`

#### `MirrorOutput` (implementado)

Comportamento atual, em `core/outputs/mirror.py`:
- repassa cada chamada para todos os outputs, na ordem recebida
- `open_phase`, `write`, `comment` e `flush` param no primeiro erro
- `finish` e `close` chegam a todos os outputs mesmo que um falhe e relançam o primeiro erro
- combinações previstas: arquivo + banco, arquivo + stdout

#### `DirectDatabaseOutput` (implementado)

Executaria direto no destino, durante a geração:
- recebe SQL ou operação estruturada
- controla transações
- pode fazer commit por lote
- exige estratégia de rollback e observabilidade
- deve oferecer um modo de dry run (ver avaliação abaixo)

Ao detalhar o desenho apareceu um conflito com a geração da época. O `load_reference_map()` lia o destino enquanto os inserts eram gerados. Com os comandos executados durante a geração, essa leitura veria uma tabela já truncada, se usasse a mesma conexão do output. Numa conexão separada, ficaria esperando o lock do `TRUNCATE` da própria transação. Além disso, o `02_dump_compatible_tables.sql` não passa pelo output, porque o `pg_dump` grava o arquivo direto.

O item 7 resolveu a primeira parte: os mapas de referência são carregados antes da primeira fase e as leituras do destino ficam fechadas depois disso. A `desacoplar-migracao-de-banco-e-versao` resolveu a segunda: a saída do `pg_dump` entra pelo `include_file()` do output. A change `introduzir-direct-database-output` implementou o `DirectDatabaseOutput`: ele executa as operações pela sessão do adapter de destino, em transação única, com os modos, as políticas e as capacidades do `ArtifactApplier`. Ele é escolhido por `--output direct` ou `--output hybrid`. Na AcmeEdu, o `hybrid --dry-run` e o `Aplicar.py` sobre os mesmos arquivos contaram os mesmos comandos em todas as fases. O tempo não caiu: `direct --apply` levou 5,2 s, contra 3,7 s de gerar e aplicar em sequência. O ganho é a falha antecipada e não precisar do `03` em disco.

#### `ManifestOutput`

Além dos comandos, gera um manifesto:
- Acme
- data/hora
- adapters usados
- quantidade de linhas por step
- hash dos artefatos

### Dry run do `DirectDatabaseOutput` (avaliação)

A ideia é rodar a migração contra o destino e descobrir o que falharia antes da execução de fato, sem deixar alteração no banco.

Esta avaliação foi escrita antes da implementação. A opção 1 foi implementada no `SqlArtifactApplier`, que aplica os arquivos depois da geração em vez de executar dentro de um output. O resultado está em [O que foi implementado](#o-que-foi-implementado).

Na época, o `rodar2.sh` aplicava cada arquivo com `psql` em autocommit e sem `ON_ERROR_STOP`. Um erro não interrompia a carga: o comando que falhou era pulado, o resto era aplicado e os erros eram filtrados do log depois. Quando algo dava errado, o destino ficava carregado pela metade e a saída era recriar a base a partir do dump de homologação, como descrito em [execucao.md](execucao.md). O dry run resolve exatamente isso: mostrar os erros antes de tocar no destino real.

#### Opção 1: transação com rollback

O output abre uma transação, executa todas as fases e termina com `ROLLBACK`. O PostgreSQL tem DDL transacional, então `TRUNCATE`, `ALTER TABLE ... DISABLE TRIGGER`, o `UPDATE pg_index` e os `INSERT`s são desfeitos. É o modo mais próximo da execução real sem precisar de outro ambiente. O desenho precisa tratar alguns limites:

- Sequences não voltam no rollback. O `setval` da fase `finalize_target`, os `setval` que o `pg_dump` grava em `02_dump_compatible_tables.sql` e o `nextval` de colunas com default ficariam aplicados. No dry run o output deve pular `setval` ou trocá-lo por uma checagem de que a sequence existe.
- O `TRUNCATE` segura `ACCESS EXCLUSIVE` até o fim da transação. As tabelas ficam bloqueadas durante todo o dry run, então ele só serve para destino sem uso concorrente.
- O primeiro erro aborta a transação. Para listar todos os erros, como o log do `psql` faz hoje, é preciso `SAVEPOINT`. Um savepoint por comando (o que o `psql` faz com `ON_ERROR_ROLLBACK`) gera milhões de subtransações na fase de carga. Acima de 64 subtransações numa mesma transação o PostgreSQL perde o cache de subtransações, o que deixa outras sessões e réplicas mais lentas. Um savepoint por lote reduz o número de subtransações, mas não mantém a transação abaixo desse limite: uma subtransação que gravou dados continua na lista até o fim, mesmo depois do `RELEASE SAVEPOINT`. Uma carga de 1 milhão de comandos em lotes de 1000 cria 1000 subtransações. O aplicador usa savepoint por lote só na política `continue`: se o lote falhar, ele volta ao savepoint e reexecuta o lote comando a comando para achar as linhas com erro. O efeito no cache precisa ser medido numa carga real antes de usar `continue` num banco com réplica.
- O dry run faz o mesmo I/O e gera o mesmo WAL da carga real para jogar tudo fora no fim.
- A fase `02_dump_compatible_tables.sql` não passava pelo output, porque o `pg_dump` gravava o arquivo direto. Desde a fase 5 da `desacoplar-migracao-de-banco-e-versao`, a fase se chama `02_copy_compatible_entities` e a saída do `pg_dump` entra pelo `include_file()`. Se ela ficar fora da transação, a carga roda sobre um destino sem os dados do dump e o resultado diverge da execução real.

#### Opção 2: execução real em cópia descartável

O mesmo `DirectDatabaseOutput`, em modo normal, apontado para um clone do destino: `CREATE DATABASE ... TEMPLATE` (exige que ninguém esteja conectado à base de origem), restore do dump de homologação ou o `target-db` do Docker. O output não precisa de nada novo, porque o dry run vira só outra conexão. É a opção mais fiel, já que commits e sequences se comportam como na execução real. O tempo medido também serve de estimativa para a janela de carga. O custo é ter onde clonar e esperar o clone. O reset manual descrito em `execucao.md` já é uma versão disso.

#### Complemento: validação sem execução

`EXPLAIN` sem `ANALYZE` não executa o comando, mas valida tabela, colunas e conversão de literais para o tipo da coluna em `INSERT`, `UPDATE` e `DELETE`. Não cobre DDL nem constraints como `NOT NULL` e FK. Também não pega erros que dependem do estado do banco, como chave duplicada. Pode rodar sobre uma amostra de cada fase contra o destino real sem bloquear tabela, junto com um resumo de quantos comandos cada fase gera por tabela.

#### Recomendação

Vale ter dry run, implementado como modo do próprio output, por exemplo `DirectDatabaseOutput(conexao, mode='apply' | 'rollback')`. Na change do `DirectDatabaseOutput`, a entrega seria o modo `rollback` com savepoint por lote, `setval` pulado e relatório de erros por fase e comando em `erros/`, restrito a destino sem uso concorrente. A cópia descartável deve ser documentada como validação preferida antes de cargas completas, porque não exige código novo. A validação por `EXPLAIN` fica para depois, caso o modo `rollback` se mostre caro demais para uso frequente.

Com o `MirrorOutput` já implementado, a combinação natural é `MirrorOutput(SqlFileOutput, DirectDatabaseOutput(mode='rollback'))`: uma única execução grava os arquivos para auditoria e valida contra o banco.

Passar no dry run não garante a execução real. O destino pode mudar entre as duas execuções. No desenho original, a transação única do dry run também diferia dos commits por lote do modo normal. No aplicador, os dois modos usam a mesma transação única.

Os dois pontos de contrato que o dry run exigia já foram resolvidos na Fase 2:

- O output recebe o resultado por `finish(success)`, chamado pelo runner uma vez por execução. O `DirectDatabaseOutput` faz `COMMIT` no `finish(True)` e `ROLLBACK` no `finish(False)` ou num `close()` sem `finish(True)`. No modo `rollback`, o `finish(True)` também termina em `ROLLBACK`.
- O manifesto grava `describe()` na chave `output`. O `DirectDatabaseOutput` deve incluir o modo na descrição, por exemplo `{"type": "direct-db", "mode": "rollback"}`, para que uma execução validada não seja confundida com uma aplicada.

Se o item 11 (`ExecutionAdapter`) avançar, esse modo vira a implementação de `dry_run()` para a estratégia `direct-db`.

#### O que foi implementado

A recomendação foi seguida com uma diferença: o modo `rollback` virou o modo `dry-run` de um aplicador que roda depois da geração, no lugar de um output. O `SqlArtifactApplier` aplica os arquivos de `sql/` numa transação única, pula `setval` no dry run e grava o relatório em `aplicacoes/<timestamp>-<modo>/`, fora do manifesto da geração. Assim uma validação não se confunde com uma carga. A mesma execução pode receber um dry run e depois o `apply`.

O teste contra um PostgreSQL 16 mostrou um limite que a avaliação não previa: num `apply` que falha, os `setval` que já rodaram continuam valendo depois do `ROLLBACK`. Adiar os `setval` para o fim não resolve, porque os inserts do `03` que usam o default da coluna pegariam ids já usados pelo dump. O efeito ficou aceito: a próxima carga roda os mesmos `setval`. Uma sequence adiantada só deixa buraco na numeração.

A combinação `MirrorOutput(SqlFileOutput, DirectDatabaseOutput(mode='rollback'))` deixou de ser o caminho para validar uma execução. Ela continua como trabalho futuro para aplicar em streaming, sem esperar a geração terminar. Depende do item 7.

### O que já mudou na v1

- `BaseMigration.write_sql()` deixou de escrever em arquivo e delega para `context.output`
- o buffer passou a ser responsabilidade do `SqlFileOutput`
- o `MigrationRunner` continua definindo as fases, mas delega a abertura de cada uma ao output
- `context.writer` e `SqlArtifactWriter` foram removidos; `BaseMigration.write_sql()`, `print_comment()` e `_open_sql_file()` continuam como API da fachada por herança

### O que mudou na Fase 2

- o contrato ganhou `finish(success)`, chamado pelo `MigrationRunner` uma vez por execução
- o contrato ganhou `describe()`, e o manifesto grava a descrição do output na chave `output`
- `MirrorOutput` entrega os mesmos comandos para mais de um output
- `BaseMigration.dump_tables()` deixou de depender de `self.file`

### O que ainda precisa mudar

- medir a política `continue` numa carga real antes de usá-la num banco com réplica
- medir se o túnel SSH até o destino derruba a conexão ociosa numa geração longa com `--output direct`
- medir o output direto numa carga real e decidir se vale agrupar operações para reduzir o custo por comando
- aplicar em paralelo, por várias conexões, depois que a aplicação tiver checkpoint por etapa (ver itens 12 e 13): sem isso, uma falha deixaria parte da carga confirmada sem saber qual

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

As opções `--apply` e `--dry-run` já existem e aplicam a execução depois da geração. Se o `--output` vier, o dry run de um output direto precisa de outro nome para não se confundir com elas.

### Riscos

- execução direta aumenta o impacto de erros lógicos
- perde-se parte da auditabilidade se não houver manifesto/log forte
- algumas operações hoje pensadas como texto SQL talvez precisem virar operações estruturadas no futuro

### Estratégia recomendada

Fase 1 (output adapter v1, implementada):
- manter SQL em arquivo como padrão
- introduzir `OutputAdapter` sem mudar o comportamento externo

Fase 2 (implementada):
- adicionar `MirrorOutput`
- incluir no contrato o resultado da execução

Fase 3 (implementada pela change `aplicar-artefatos-sql-no-destino`):
- aplicar os arquivos de uma execução no destino em transação única, com dry run e relatório
- `Aplicar.py` e as opções `--apply` e `--dry-run` no `Migracao.py`

Fase 4 (implementada pela change `introduzir-direct-database-output`):
- adicionar `DirectDatabaseOutput` para aplicar durante a geração
- permitir escolher o output pelo CLI

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

Feito pela change `padronizar-lookups-de-referencia`:

- o cliente declara os mapas em `declare_reference_maps()`, com `ReferenceMap` (tabela, chave, valor e filtros de igualdade, ou uma `query` do destino). A leitura é com `self.reference_map(nome)`;
- o runner carrega todos antes da fase `prepare_target` e fecha as leituras do destino logo depois. O provider passa a recusar consultas e encerra a transação de leitura;
- a normalização, o descarte de chave nula e a recusa de chave ambígua ficam no core, iguais para qualquer provider. O mapa carregado guarda o normalizador e o `Lookup` reaproveita;
- saíram `load_reference_map()` do contexto, da fachada e do provider, junto com o cache `_reference_maps`.

Ficou de fora: chave composta, que hoje se resolve concatenando no `query`. A garantia de não ler o destino no meio da carga também não cobre consultas feitas direto por `pg_v2_conn`.

---

## 8 — Revisar estratégias e expressões dependentes de dialeto

Feito pela change `revisar-estrategias-dependentes-de-dialeto`:

- as strategies devolvem só valores Python pelo `raw_value()`. Saíram `value()`, `render()` e a função `sql_value()` de `core/strategies.py`. O adapter de destino passou a ser o único ponto de serialização;
- o `SqlExpression` virou `SourceSqlExpression`, com o dialeto da origem obrigatório e conferido antes de qualquer consulta (depois trocado pelo `NativeField`, na `desacoplar-migracao-de-banco-e-versao`). Os dialetos ganharam nome (`postgres`, `firebird`);
- o `RegexClean` deixou de montar `E'...'` à mão e de remover a barra invertida. Ganhou também `v2_name`;
- o `DateConvert` devolve `datetime.date` ou `datetime.datetime` e perdeu o `to_fmt`;
- entraram as strategies portáveis `NullIfBlank`, `Truncate`, `Upper`, `Lower` e `Default`. O `regexp_replace` é coberto pelo `RegexClean`;
- o `docs/estrategias.md` separa as strategies portáveis das dependentes de dialeto.

### Tipos especiais na serialização

Feito pela change `serializar-tipos-especiais-no-dialeto`. O `PostgresDialect` passou a aceitar uma lista fechada de tipos:

- binários (`bytes`, `bytearray`, `memoryview`) viram `decode('<hex>', 'hex')`, que não depende do `standard_conforming_strings`;
- `NaN` e infinitos viram literais com cast (`'NaN'::float8`, `'Infinity'::numeric`). O `Decimal` finito sai sem aspas;
- datas, horas e `UUID` saem em texto ISO 8601 ou canônico, com o mesmo literal de antes para `date` e `datetime`;
- texto com NUL e qualquer tipo fora da lista fazem a geração falhar, com a tabela, a coluna e o tipo na mensagem, sem o valor.

Uma coluna que recebe binário gera um aviso no log, uma vez por tabela e coluna, porque numa coluna `text` o valor vira a forma hexadecimal sem erro.

Continuam sem tratamento automático e falham na geração: `timedelta`, `list` e `dict`. Os dois últimos podem ser array ou JSON, dependendo da coluna de destino. Se esses tipos aparecerem nos clientes, cabem strategies próprias, como uma `Json` que faça `json.dumps`.

---

## 9 — Limpar código legado e utilitários obsoletos

Feito pela change `limpar-codigo-legado`. Saíram:

- `common/`, com `dateutil.py` e `regexutil.py`, que não tinham uso e tinham defeito;
- `core/engine.py`, que só reexportava classes dos módulos atuais. O `core/__init__.py` ficou como estava;
- os doze helpers privados de engine, progresso e TUI do `BaseMigration`. O engine vem só do contexto, hoje por `self.context.source(nome)`;
- os imports de `psycopg2` e `firebirdsql` do `core/base_migration.py`, que não eram usados;
- `queries_duplicidade_dados.sql`, que atualizava tabelas que o `Acme` não migra.

A configuração de depuração do VS Code passou a apontar para o container `project-hermes`.

Ficaram de fora, porque outras changes cuidam deles:

- `FieldStrategy.value()`, `sql_value()` e o `RegexClean`, na `revisar-estrategias-dependentes-de-dialeto`;
- `load_reference_map()`, na `padronizar-lookups-de-referencia`;
- as conexões `pg_v1_conn`, `pg_v2_conn` e `fb_v1_conn`, na `desacoplar-migracao-de-banco-e-versao`. O `rodar2.sh` saiu depois dela, porque o `Aplicar.py` com `--on-error continue` faz o mesmo numa transação e lista os erros.

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

A change `paralelizar-geracao-de-tarefas-independentes` implementou o paralelismo entre tarefas. O script do cliente declara tarefas com `@task(nome, sequence=1)`. As da mesma sequência rodam juntas com `--workers N`, em threads ou em processos (`--worker-mode`). O processo principal junta o resultado delas ao arquivo da fase na ordem declarada, sem mudar os artefatos. Na AcmeEdu, os artefatos saíram iguais com 1, 2 e 4 workers nos dois modos. O tempo não caiu de forma clara, porque a geração dela leva cerca de 1 s contra um banco local.

Ficam para depois: medir numa carga real com a origem remota, a leitura em streaming, o paralelismo dentro de uma tarefa (por faixa de chave ou partição), dependências por `after=` além da sequência e a aplicação paralela, que depende de checkpoint na aplicação.

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

A parte da geração está proposta na change `registrar-checkpoints-da-geracao`, que depende da de paralelismo. O nome de cada tarefa vira a identidade do checkpoint. O `checkpoint.json` guarda a posição de cada tarefa no arquivo da fase. Com `--resume`, uma geração que falhou retoma na mesma pasta, pulando as tarefas concluídas, desde que a impressão digital da execução e a contagem da origem não tenham mudado. A aplicação continua tudo ou nada: checkpoint na aplicação fica para depois.

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

### O que foi implementado

A change `registrar-metricas-da-geracao` entregou a primeira parte:

- o `relatorio-geracao.json`, com linhas lidas, inserts por entidade, duração e situação de cada unidade, duração e totais de cada fase, cópias nativas e tamanho dos artefatos;
- a contagem de perda de dado no `Lookup`, no `DateConvert` e no `EmailExtract`, com até 20 exemplos por grupo;
- o `logs/migration.jsonl`, com execução, nível, fase e unidade em cada evento;
- o resumo no fim do `migration.log`;
- os níveis `info`, `warning` e `error` no `print_log()`.

Na `AcmeEdu`, o relatório mostrou 50 datas de nascimento e 61 datas de matrícula impossíveis que viravam `NULL` sem aviso. Ficam para depois: tempo por lote, vazão, memória, novas tentativas, o nível `debug`, perdas nas estratégias de limpeza, a opção de gravar só contagens sem exemplos, comparação entre origem e destino fora da `AcmeEdu` e exportação em Markdown ou HTML.

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

1. Introduzir contratos de `SourceAdapter`, `TargetAdapter` e `OutputAdapter` (feito)
2. Adaptar `BaseMigration` para depender desses contratos sem mudar o comportamento externo (feito)
3. Manter `SqlFileOutput` como padrão e extrair o buffer atual para ele (feito)
4. Migrar Acmes restantes para o `core/`
5. Reduzir SQL manual residual
6. Adicionar `MirrorOutput` (feito) e a aplicação dos arquivos com dry run (feito). O `DirectDatabaseOutput` espera a padronização dos lookups (melhoria 7)
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
