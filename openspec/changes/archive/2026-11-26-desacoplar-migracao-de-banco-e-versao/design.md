## Context

O core já tem contratos de adapter (`SourceAdapter`, `TargetAdapter`, `Dialect`, `MetadataProvider`, `OutputAdapter`), mas todos foram desenhados em volta de SQL e do PostgreSQL. O levantamento feito para esta change encontrou acoplamento em todas as camadas:

| Camada | Acoplamento |
|---|---|
| Configuração | `core/context.py` e `core/base_migration.py` importam `psycopg2` e `firebirdsql` ao carregar. O contexto sempre conecta `PG_V1_*` e `PG_V2_*` e fixa `PostgresTargetAdapter`, `PostgresMetadataProvider` e `SqlFileOutput`. O manifesto só conhece `PG_V1`, `PG_V2` e `FB_V1`. |
| Origem | `SourceCursor.execute(sql)`, `scalar(sql)`, `limit_clause('LIMIT n')`. O `COUNT(*)` do progresso é SQL montado em Python. O Firebird reescreve `LIMIT` para `ROWS` com regex. |
| API do cliente | `source_sql` com `{fields}` e `{limit}`, `SqlExpression`, `write_sql`, `load_reference_map(sql)`, `TRUNCATE` escrito à mão, `pg_v1_conn` e afins expostos. |
| Destino | `insert_statement` devolve texto SQL. O `MetadataProvider` devolve SQL de `pg_tables`, `UPDATE pg_index` e `setval('{tabela}_{coluna}_seq')`, com `f_id` como padrão. |
| Artefatos | Cinco fases fixas em arquivos `.sql`. A fase `02` só existe pelo `pg_dump`. |
| Versão | `postgresql-client-9.6` fixo no `Dockerfile`. O `pg_dump` recusa servidor mais novo que ele, por isso a V1 precisa ser 9.6. |
| Aplicação | `core/apply` usa `psycopg2`, o lexer do PostgreSQL, `SAVEPOINT`, `RESET ALL` e `SHOW standard_conforming_strings`. O `Aplicar.py` lê as variáveis da libpq. |

Duas propostas do branch `doc/propostas-melhorias-futuras` resolvem partes disso e são pré-requisitos desta change. A `revisar-estrategias-dependentes-de-dialeto` faz as strategies devolverem só valores Python e obriga o SQL de origem a declarar o dialeto. A `padronizar-lookups-de-referencia` troca o SQL livre dos mapas de referência por declarações.

## Goals / Non-Goals

**Goals:**

- Qualquer banco com driver em Python, relacional ou NoSQL, serve de origem ou de destino quando existe um adapter, sem mudança no core.
- Nenhuma versão de banco ou de ferramenta fixa no core, no `Dockerfile` ou nos requisitos.
- O cliente escreve a migração sem SQL no caso comum e declara explicitamente quando usa a linguagem de um banco específico.
- Um adapter MongoDB nos dois lados prova que os contratos servem para NoSQL.

**Non-Goals:**

- Escrever adapters para outros bancos além de PostgreSQL, Firebird e MongoDB.
- Otimizar a carga (`COPY`, `insert_many` fora de transação, paralelismo). Isso é assunto do item 11 do roadmap.
- Manter compatibilidade com a API e o `.env` atuais. O projeto não está em produção.
- Mudar o comportamento das strategies e dos mapas de referência além do que as duas changes pré-requisito já fazem.

## Decisions

1. **Conexões nomeadas no `.env`.**

   Cada conexão é um grupo de chaves com o mesmo prefixo. O nome da origem é o trecho entre `SOURCE_` e `_ADAPTER`, em minúsculas. Os outros sufixos do grupo viram os parâmetros do adapter.

   ```bash
   SOURCE_V1_ADAPTER=postgres
   SOURCE_V1_HOST=acmeedu-v1
   SOURCE_V1_PORT=5432
   SOURCE_V1_NAME=acmeedu_v1
   SOURCE_V1_USER=acmeedu
   SOURCE_V1_PASS=acmeedu

   SOURCE_LEGADO_ADAPTER=firebird
   SOURCE_LEGADO_HOST=...

   TARGET_ADAPTER=mongodb
   TARGET_URI=mongodb://acmeedu-v2:27017/acmeedu_v2?replicaSet=rs0
   ```

   O cliente pede a origem pelo nome: `self.context.source('v1')`. Cada adapter valida os próprios parâmetros e cita os que faltam no erro. Não há origem padrão nem nomes fixos como `pg_v1` e `fb_v1`.

   Alternativa considerada: manter `PG_V1_*`, `PG_V2_*` e `FB_V1_*` com um `*_ADAPTER` ao lado. Rejeitada porque o nome da chave continuaria dizendo um banco que pode não ser o configurado.

2. **Registro de adapters com driver carregado sob demanda.**

   `core/adapters/registry.py` resolve o nome do `.env` para a classe do adapter:

   | Nome | Origem | Destino | Driver |
   |---|---|---|---|
   | `postgres` | sim | sim | `psycopg2` |
   | `firebird` | sim | não | `firebirdsql` |
   | `mongodb` | sim | sim | `pymongo` |

   Qualquer outro valor no formato `pacote.modulo:Classe` é importado com `importlib`. É assim que entra um adapter de fora do core. O registro confere se a classe tem os métodos do contrato antes de usar. O driver só é importado dentro do módulo do adapter, na conexão. Um cliente que só usa PostgreSQL não precisa do `firebirdsql` nem do `pymongo` instalados.

3. **Leitura neutra.**

   ```python
   @dataclass(frozen=True)
   class SourceQuery:
       entity: str                      # tabela, coleção
       fields: tuple = ()               # nomes ou NativeField; vazio lê todos
       where: Mapping[str, Any] = {}    # igualdade, combinada com E
       order_by: tuple[str, ...] = ()
       limit: int | None = None

   @dataclass(frozen=True)
   class NativeQuery:
       adapter: str                     # adapter que entende a consulta
       query: Any                       # texto SQL, pipeline do MongoDB...
       limit: int | None = None

   class SourceAdapter(Protocol):
       name: str
       server_version: str
       def read(self, query) -> Iterator[Mapping[str, Any]]: ...
       def count(self, query) -> int | None: ...
       def close(self) -> None: ...
   ```

   O filtro só tem igualdade de propósito. Cobre os casos comuns e cabe em qualquer banco. O que passar disso (junção, `LATERAL`, expressão regular, agregação) vai em `NativeQuery`. Um campo calculado vai em `NativeField(adapter, expression, alias)`, que substitui o `SourceSqlExpression` da change de strategies. O adapter recusa `NativeQuery` e `NativeField` de outro adapter antes de executar, com mensagem que cita os dois nomes.

   Num campo com ponto (`endereco.cidade`), o adapter de documento lê o caminho e devolve a chave com o mesmo nome. O adapter relacional recusa.

   O modo debug passa a ser um limite inteiro aplicado pelo contexto a toda consulta, no lugar da string `LIMIT n`. A contagem do progresso fica com o adapter (`COUNT(*)` no SQL, `count_documents` no MongoDB).

   `TableMigration`, `ExpandMigration` e `MultiTargetMigration` recebem `source=` com uma das duas consultas. Quando a consulta é um `SourceQuery` sem campos, os campos saem das strategies, como o `{fields}` faz hoje.

4. **Escrita neutra por operações.**

   ```python
   Insert(entity, values: Mapping[str, Any])
   Clear(entities)                          # apaga os dados, mantém a estrutura
   SuspendConstraints(entities=())          # vazio: todas
   ResumeConstraints(entities=())
   ResetIdentity(entity, column='id')
   Native(adapter, payload)                 # comando na linguagem de um banco
   Comment(text)
   ```

   ```python
   class TargetAdapter(Protocol):
       name: str
       server_version: str
       capabilities: Capabilities
       artifact_format: ArtifactFormat
       def render(self, operation) -> list[str]: ...           # zero ou mais comandos
       def read(self, query) -> Iterator[Mapping[str, Any]]: ...
       def session(self) -> TargetSession: ...
       def close(self) -> None: ...
   ```

   O adapter traduz cada operação para comandos do formato dele. Uma operação sem sentido no banco gera lista vazia: no MongoDB, `SuspendConstraints`, `ResumeConstraints` e `ResetIdentity` não geram nada. `Native` de outro adapter é recusada.

   No PostgreSQL, `ResetIdentity` usa `pg_get_serial_sequence(tabela, coluna)` em vez de montar o nome `<tabela>_<coluna>_seq`, o que tira a convenção `f_id` do core. `SuspendConstraints` lê o catálogo do destino para listar as tabelas, como o `MetadataProvider` faz hoje.

   O `MetadataProvider` sai. A leitura de mapas de referência declarados pela change de lookups passa pelo `read()` do adapter de destino, com um `SourceQuery` montado a partir da declaração.

   O helper de índices (`set_index_commands_for`) também sai, sem substituto. A carga da AcmeEdu mostrou que ele não faz nada quando recebe nome de índice e deixa o índice sem as linhas carregadas quando recebe nome de tabela. Desligar índices durante a carga volta, se precisar, como otimização de um adapter.

   A fachada `BaseMigration` troca `write_sql`, `print_comment` e `sql_value` por `emit(operacao)` e por atalhos: `self.clear(...)`, `self.suspend_constraints()`, `self.reset_identity(...)`.

5. **Artefatos no formato do destino.**

   Cada adapter de destino declara um `ArtifactFormat` com extensão, gravação e leitura de comandos:

   | Adapter | Extensão | Um comando é |
   |---|---|---|
   | `postgres` | `.sql` | um comando SQL terminado em `;`, lido pelo separador léxico atual |
   | `mongodb` | `.jsonl` | uma linha em Extended JSON relaxado, como `{"op": "insert", "collection": "people", "document": {...}}` |

   Os arquivos ficam em `comandos/<NN>_<fase>.<extensão>`, no lugar de `sql/`. As fases continuam as mesmas, com a `02` renomeada para `02_copy_compatible_entities`.

   O manifesto passa a registrar cada conexão sem senha, com adapter e versão do servidor. Também registra a lista de artefatos com fase, caminho e formato:

   ```json
   "sources": {"v1": {"adapter": "postgres", "server_version": "17.2"}},
   "target": {"adapter": "mongodb", "server_version": "7.0.14", "format": "jsonl"},
   "artifacts": [{"phase": "load_transformed_data", "path": "comandos/03_load_transformed_data.jsonl", "format": "jsonl"}]
   ```

   O `OutputAdapter` recebe operações (`write(operation)`). O `FileOutput`, sucessor do `SqlFileOutput`, pede ao adapter de destino os comandos de cada operação e grava no arquivo da fase. O `MirrorOutput` não muda de comportamento.

6. **Cópia genérica das entidades compatíveis, com `pg_dump` opcional.**

   O hook `compatible_entities()` devolve a lista do que passa sem transformação, com renomeação opcional (`CopyEntity('holidays', target='feriados')`). O core lê cada entidade pela origem e emite um `Insert` por linha na fase `02`. Funciona para qualquer par de adapters.

   A lista de inclusão substitui `dump_ignoring_tables()` e `dump_non_existing_tables()`. Com exclusões, o cliente precisa conhecer toda tabela e toda sequence da V1. Uma tabela nova na V1 entra no dump sem ninguém ver. A AcmeEdu lista 14 exclusões para copiar uma tabela.

   O adapter PostgreSQL de origem oferece um atalho opcional quando o destino também é PostgreSQL: com `SOURCE_V1_NATIVE_COPY=pg_dump` (e `SOURCE_V1_PG_DUMP` com o caminho do binário, se não estiver no `PATH`), a fase `02` sai do `pg_dump` das mesmas entidades. Antes de rodar, o adapter compara a versão principal do `pg_dump` com a do servidor e falha com as duas versões na mensagem se o binário for mais antigo. Large objects (`-b`) só existem nesse atalho.

7. **Aplicação guiada por capacidades.**

   ```python
   @dataclass(frozen=True)
   class Capabilities:
       transactions: bool        # dry run e aplicação atômica
       savepoints: bool          # política continue
       suspend_constraints: bool
   ```

   O `Aplicar.py` lê do manifesto o adapter e o formato. A conexão vem das variáveis `TARGET_*`, as mesmas do `.env`, no lugar das variáveis da libpq. Para cada artefato, o aplicador lê os comandos pelo `ArtifactFormat` e executa pela `TargetSession` (`begin`, `execute`, `savepoint`, `rollback_to`, `commit`, `rollback`).

   Antes de conectar, o aplicador confere o que o modo pede. Dry run e aplicação com `stop` exigem `transactions`. A política `continue` exige `savepoints`. Se o destino não tem a capacidade, a aplicação é recusada com a capacidade que falta na mensagem.

   O que hoje é do PostgreSQL vai para dentro do adapter PostgreSQL: o separador léxico, os comandos pulados (metacomandos, controle de transação, `setval` no dry run), o `RESET ALL` entre arquivos e o `standard_conforming_strings` lido do servidor. O relatório troca `SQLSTATE` por `code`, preenchido pelo adapter (`pgcode` no PostgreSQL, código do erro no MongoDB).

8. **Versão detectada e faixa declarada.**

   Cada adapter lê a versão do servidor ao conectar (`server_version_num` no PostgreSQL, `ENGINE_VERSION` no Firebird, `buildInfo` no MongoDB), grava no manifesto e confere a faixa que declara para cada lado. Fora da faixa, a execução para antes da geração com as duas informações na mensagem.

   | Adapter | Origem | Destino | Motivo do limite |
   |---|---|---|---|
   | `postgres` | 9.6 ou mais novo | 12 ou mais novo | a V1 real roda 9.6; `pg_get_serial_sequence` e o catálogo usados no destino |
   | `firebird` | 2.5 ou mais novo | não se aplica | `ROWS` e `ENGINE_VERSION` |
   | `mongodb` | 4.4 ou mais novo | 4.4 ou mais novo | transações em replica set |

   Diferença de comportamento entre versões fica em método do adapter, não em comparação de versão espalhada pelo core.

   O `Dockerfile` deixa de instalar `postgresql-client-9.6`. Quem usa o atalho do `pg_dump` escolhe a versão por um argumento de build. Os drivers saem do `requirements.txt` comum para `requirements/postgres.txt`, `requirements/firebird.txt` e `requirements/mongodb.txt`. A imagem do Hermes instala os três.

9. **Adapter MongoDB.**

   - Origem: `SourceQuery` vira `find` com filtro, projeção, ordenação e limite. `NativeQuery` recebe `{"collection": ..., "pipeline": [...]}` e vira `aggregate`. A contagem usa `count_documents` ou um `$count` no fim do pipeline. O `_id` só vem quando pedido.
   - Destino: `Insert` vira uma linha `insert` em Extended JSON relaxado, que preserva data, `Decimal128` e binário. `Clear` vira `delete_many({})`, que mantém índices e validadores. As operações de restrição e identidade não geram nada.
   - Aplicação: a sessão abre uma transação e agrupa inserts seguidos na mesma coleção em `insert_many` de até 1000 documentos. Um erro de `BulkWriteError` é ligado de volta ao número do comando no artefato.
   - Capacidades: `transactions` só quando o servidor está num replica set (conferido no `hello`), `savepoints` falso, `suspend_constraints` falso.

10. **AcmeEdu neutra e matriz de prova.**

    O `AcmeEdu.py` deixa de ter SQL próprio. O `TRUNCATE`, as triggers e o `setval` viram `clear()`, `suspend_constraints()` e `reset_identity()`. O agrupamento de professores por CPF deixa de usar `LEFT JOIN LATERAL`: o script lê os CPFs dos alunos com um `SourceQuery`, monta o mapa em Python e resolve o `person_id` de cada professor. Assim o mesmo script roda com a V1 em PostgreSQL ou em MongoDB e com a V2 em qualquer um dos dois.

    O `conferir.py` lê as duas bases pelos adapters. As checagens próprias de um banco (sequences e `amcheck`) só rodam quando o destino é PostgreSQL.

    | V1 | V2 | O que prova |
    |---|---|---|
    | PostgreSQL 9.6 | PostgreSQL 16 | o caminho atual continua igual |
    | PostgreSQL 17 | PostgreSQL 17 | nenhuma versão fixa |
    | PostgreSQL 17 | MongoDB 7 | destino NoSQL |
    | MongoDB 7 | PostgreSQL 17 | origem NoSQL |
    | Firebird 3 e 5 | PostgreSQL 17 | origem Firebird em duas versões, com uma fixture pequena |

    A V1 em MongoDB e a fixture do Firebird são geradas a partir da V1 em PostgreSQL por um script de apoio, para os dados serem os mesmos.

## Risks / Trade-offs

- A change mexe em quase todo o `core/` → as tasks vêm em fases que deixam o sistema rodando ao fim de cada uma. Cada fase pode virar um PR. Se uma fase crescer demais na implementação, ela vira uma change própria.
- Os clientes privados precisam ser reescritos → a documentação ganha um roteiro de conversão do `.env` e da API, com a AcmeEdu como exemplo antes e depois.
- A cópia genérica da fase `02` lê linha a linha pela origem, o que pode ser mais lento que o `pg_dump` em tabelas grandes → o atalho do `pg_dump` continua disponível entre PostgreSQLs.
- A transação do MongoDB tem limite de tempo (`transactionLifetimeLimitSeconds`, 60 segundos por padrão) → uma carga grande estoura o limite. O adapter lê o parâmetro do servidor e o compose da prova aumenta o valor. Ver Open Questions.
- O filtro só com igualdade empurra casos mais ricos para `NativeQuery` → aceito. A consulta nativa fica explícita e presa ao adapter declarado, que é o objetivo.
- O Extended JSON é menos legível que SQL para revisão manual → aceito, porque preserva os tipos que o JSON comum perde.

## Migration Plan

1. Arquivar `aplicar-artefatos-sql-no-destino`, `preservar-barra-invertida-nos-literais` e `criar-cliente-ficticio-educacional`. Implementar as duas changes pré-requisito.
2. Registro, conexões nomeadas e drivers sob demanda, com PostgreSQL e Firebird adaptados.
3. Leitura neutra.
4. Operações de escrita, `FileOutput`, artefatos por formato e manifesto novo.
5. Cópia de entidades compatíveis e atalho do `pg_dump`.
6. Aplicação por capacidades.
7. Adapter MongoDB.
8. AcmeEdu neutra, fixtures e matriz.
9. Documentação e roteiro de conversão dos clientes.

Rollback: cada fase é um conjunto de commits que pode ser revertido sozinho até a fase 4. Da fase 4 em diante, os artefatos mudam de pasta e de formato. Voltar exige reverter a partir dela.

## Open Questions

- Um destino sem transação, como um MongoDB fora de replica set, deve poder receber a carga sem atomicidade com uma opção explícita (`--sem-transacao`)? A proposta hoje recusa.
- O `rodar2.sh` continua no repositório? Ele só serve para artefatos `.sql` e o `Aplicar.py` cobre o mesmo caso.
- Vale incluir agora um segundo adapter relacional de destino (MySQL ou SQL Server) para provar que o contrato não ficou com cara de PostgreSQL? A proposta deixa de fora.
