# Arquitetura da Migração Acme

## Visão geral

O script `Migracao.py` é uma aplicação CLI que:

1. Lê a configuração do Acme em `Acme/.env`.
2. Abre conexão com três bancos.
3. Gera arquivos `.sql` com `DELETE`, `UPDATE`, `INSERT`, `ALTER TABLE` e `setval`.
4. Não aplica os dados na V2 durante a geração.
5. Deixa a aplicação para depois, sobre os arquivos gerados em `runs/<cliente>/<timestamp>/sql/`. Quem aplica é o `Aplicar.py` (ou o próprio `Migracao.py` com `--apply` ou `--dry-run`), numa única transação. O `rodar2.sh` com `psql` continua como alternativa.

## Bancos envolvidos

O script usa três conexões independentes:

- `PG_V1_*`: PostgreSQL da base legada V1.
- `PG_V2_*`: PostgreSQL da base destino V2.
- `FB_V1_*`: Firebird legado, usado para complementar parte das informações migradas.

O contrato novo de ambiente para o destino usa `TARGET_DB_*` e o hostname Docker
`target-db`. As variáveis `PG_V2_*` ainda são necessárias porque o core atual
abre conexões PostgreSQL diretamente; elas devem espelhar `TARGET_DB_*` até a
introdução dos adapters.

Resumo do papel de cada origem:

- `PG_V1`: principal fonte dos `SELECT`s e do `pg_dump`.
- `PG_V2`: fonte de metadados do ambiente destino, especialmente para listar tabelas e gerar comandos de trigger.
- `Firebird V1`: fonte auxiliar para algumas rotinas específicas do Acme.

## Estrutura lógica do script

O script de cada cliente declara a migração, mas o runtime compartilhado é
montado por composição. A classe `BaseMigration` continua disponível para
compatibilidade, porém delega as responsabilidades de infraestrutura para
componentes menores.

Componentes principais:

- `MigrationContext`: agrupa configuração, artefatos, adapters de origem,
  adapter de destino, provider de metadados, output, logger e progresso.
- `DefaultMigrationContextFactory`: cria o contexto padrão a partir das chaves
  `.env` atuais (`PG_V1_*`, `PG_V2_*`, `FB_V1_*`).
- `MigrationRunner`: orquestra as fases padrão da execução.
- `SourceAdapter`: lê linhas e escalares da origem, incluindo diferenças como
  `LIMIT` no PostgreSQL e `ROWS` no Firebird.
- `TargetAdapter` e `Dialect`: renderizam `INSERT`s e valores SQL para o destino.
- `MetadataProvider`: concentra operações auxiliares do destino, como triggers,
  índices, mapas de referência e sequences.
- `OutputAdapter`: recebe o SQL gerado, fase por fase. A implementação padrão é
  `SqlFileOutput`, que grava os arquivos em `runs/<cliente>/<timestamp>/sql/`.
  O `MirrorOutput` entrega os mesmos comandos para mais de um output.
- `BaseMigration`: fachada transitória para scripts que ainda usam herança.

Responsabilidades do cliente:

- listar tabelas ignoradas ou ausentes no `pg_dump`;
- declarar SQL bruto específico do cliente quando necessário;
- declarar migrações de tabelas, transformações, inserts e updates;
- indicar índices ou sequences por meio dos helpers do core quando a operação
  for recorrente.

SQL PostgreSQL puro continua permitido no cliente como escape hatch. A regra
prática é: lógica excepcional do cliente pode ficar no Acme; operações
recorrentes de infraestrutura devem passar por adapter, dialect ou provider.

## API de composição e compatibilidade

O caminho preferido para novas integrações é receber um `MigrationContext` por
composição e usar `context.engine('pg_v1')`, `context.engine('fb_v1')`,
`context.output` e `context.metadata`. Para escrever SQL fora de um engine, use
`context.output.write(...)` e `context.output.comment(...)`. O `BaseMigration`
usa esse mesmo contexto por baixo, então os scripts antigos podem continuar
herdando da base durante a transição.

O contexto expõe a saída só por `context.output`. O antigo `context.writer` e a
classe `SqlArtifactWriter` foram removidos. Quem monta um `MigrationContext` à
mão passa o output no argumento `output=`.

Na fachada por herança, `BaseMigration.write_sql()`, `print_comment()`,
`_open_sql_file()` e `_flush_writes()` continuam disponíveis e delegam para
`context.output`, então funcionam com qualquer `OutputAdapter`. A propriedade
`file` devolve o arquivo aberto pelo `SqlFileOutput` ou `None` quando o output
não grava em arquivo.

Os atributos `pg_v1_conn`, `pg_v2_conn` e `fb_v1_conn` permanecem disponíveis
na fachada por compatibilidade. Código novo deve preferir adapters, providers e
helpers como `load_reference_map()`, `set_index_commands_for()` e
`write_reset_sequences()`.

## Saída da migração

O runtime não abre arquivos SQL por conta própria. Ele entrega os comandos para
um `OutputAdapter`, definido em `core/outputs/base.py`:

```python
class OutputAdapter(Protocol):
    def open_phase(self, phase: str) -> None: ...
    def write(self, sql: str) -> None: ...
    def comment(self, text: str) -> None: ...
    def flush(self) -> None: ...
    def finish(self, success: bool) -> None: ...
    def describe(self) -> dict: ...
    def close(self) -> None: ...
```

As fases são chaves lógicas: `setup`, `prepare_target`, `load_transformed_data`
e `finalize_target`. A `DefaultMigrationContextFactory` abre `setup` ao montar o
contexto. O `MigrationRunner` abre as outras três na ordem padrão, escreve os
comandos finais de índices e triggers e chama `flush()`. Os engines criados por
`context.engine()` escrevem pelo mesmo `output.write`.

O output fica sabendo do resultado pelo `finish(success)`, que o runner chama
uma vez por execução. Na execução que termina bem, `finish(True)` vem depois do
último `flush()` e antes de o manifesto receber `success`. Se o próprio
`finish(True)` falhar, a execução fica `failed` e o runner não chama
`finish(False)` em seguida. Quando um hook levanta exceção, o runner chama
`finish(False)` e grava `failed`. Um erro dentro desse `finish(False)` vai para
o log sem trocar o erro registrado no manifesto.

O `close()` só libera recursos e acontece em `MigrationContext.close()`, que no
caminho por herança roda a partir do `__del__` do `BaseMigration`. Por isso o
resultado não passa pelo `close()`. Um output fechado sem `finish(True)` deve
tratar a execução como não confirmada, o que para um output de banco significa
`ROLLBACK`.

O `describe()` devolve um dicionário que o manifesto grava na chave `output`.
Com o contexto padrão o valor é `{"type": "sql-file"}`.

A implementação padrão é `SqlFileOutput`, em `core/outputs/sql_file.py`. Ela
resolve cada fase pelo `ArtifactManager.sql_path()`, então os nomes de arquivo
continuam definidos em um só lugar. O SQL fica em um buffer de até 500 trechos
antes de ir para o arquivo. Trocar de fase, chamar `flush()` ou `close()` grava
o que estiver pendente. O `comment()` gera o mesmo bloco `-----` que já aparecia
nos arquivos. O `finish()` grava o buffer nos dois resultados: quando a execução
falha, os arquivos parciais ficam no disco, como antes.

O `MirrorOutput`, em `core/outputs/mirror.py`, recebe uma lista de outputs e
repassa cada chamada para todos, na ordem em que foram passados:

```python
output = MirrorOutput(SqlFileOutput(artifacts), outro_output)
```

`open_phase()`, `write()`, `comment()` e `flush()` param no primeiro erro, que
derruba a execução. `finish()` e `close()` chegam a todos os outputs mesmo que
um falhe, e o primeiro erro é relançado no fim. O `describe()` devolve
`{"type": "mirror", "outputs": [...]}` com a descrição de cada output. Com
`MirrorOutput`, a propriedade `BaseMigration.file` devolve `None`.

A `DefaultMigrationContextFactory` ainda monta só o `SqlFileOutput`. Para usar
o `MirrorOutput` hoje é preciso montar o `MigrationContext` à mão ou trocar
`context.output` antes de rodar. A escolha pelo CLI fica para quando houver
outro output concreto.

Nenhum output do core executa SQL no banco de destino. A aplicação acontece
depois da geração, pelo aplicador descrito na próxima seção. O
`DirectDatabaseOutput`, que executaria os comandos durante a geração, ficou
adiado. O motivo está em [Aplicação no destino](#aplicação-no-destino) e no
item 2 de [melhorias-futuras.md](melhorias-futuras.md).

## Aplicação no destino

O código fica em `core/apply/`:

| Módulo | Papel |
|---|---|
| `splitter.py` | separa um arquivo SQL em comandos pelas regras léxicas do PostgreSQL |
| `run.py` | lê o `manifest.json` e lista os arquivos de uma execução, recusando execuções incompletas |
| `applier.py` | `SqlArtifactApplier`: aplica os arquivos em transação única, com modo e política de erro |
| `report.py` | grava `relatorio.json` e `erros.txt` em `aplicacoes/<timestamp>-<modo>/` |
| `cli.py` | fluxo de console comum ao `Aplicar.py` e ao `Migracao.py --apply` |

### Por que depois da geração

A geração lê o destino enquanto gera os inserts: `load_reference_map()` consulta
a V2 pela conexão do `PostgresMetadataProvider`. Se os comandos fossem
executados durante a geração, essa leitura veria o destino no meio da carga.
Na mesma conexão, uma tabela truncada pelo `01_prepare_target` apareceria vazia.
Numa conexão separada, a leitura esperaria o lock do `TRUNCATE`, que só sai no
fim da transação. O processo ficaria esperando por ele mesmo. Além disso, o
`02_dump_compatible_tables.sql` é gravado pelo `pg_dump`, fora do output.

Aplicando a pasta da execução depois, o dump entra pela ordem dos arquivos e a
aplicação pode rodar em outra máquina. O `GeneratedRun` resolve os arquivos a
partir da pasta da execução. Os caminhos absolutos do manifesto não são usados.

### Separador de comandos

O `split_sql_file()` lê o arquivo linha a linha e devolve objetos `Statement`
com o tipo (`sql` ou `meta-command`), o texto sem `;` final e a linha inicial.
Um `;` só encerra o comando fora de strings `'...'` e `E'...'`, identificadores
`"..."`, dollar quotes `$tag$...$tag$` e comentários, inclusive `/* */`
aninhados. O separador acompanha os `SET standard_conforming_strings` do
arquivo, porque com essa configuração desligada a barra invertida também escapa
a aspa em `'...'`. O valor inicial vem do servidor.

Internamente é uma máquina de estados (padrão State). O `_StatementSplitter`
guarda o estado atual. Cada estado é uma classe com um método `scan()`:
`_Gap` entre comandos, `_InStatement` dentro de um comando e uma subclasse de
`_Literal` para cada tipo de literal. Para incluir um literal novo, basta criar
a classe e registrá-la em `_LITERALS`.

A separação foi comparada com a do `psql` 16 e coincidiu em todas as entradas
testadas. A diferença conhecida é a barra invertida no meio de um comando: o
`psql` executa um metacomando ali e o separador manda o texto ao servidor.

### Aplicador

O `SqlArtifactApplier(run_dir, connect, mode, on_error, batch_size=1000)`
recebe uma função `connect` e só a chama depois de validar a execução, então
uma execução incompleta não abre conexão. A conexão roda com `autocommit`
desligado e cada comando vai num `execute()`.

Duas escolhas definem uma aplicação, cada uma representada por um objeto:

- o modo (`_ApplyMode` ou `_DryRunMode`) decide como a transação termina e
  quais comandos pular;
- a política de erro (`_StopPolicy` ou `_ContinuePolicy`) decide o que fazer
  quando um comando falha.

Os comandos que não vão ao servidor estão numa tabela de regras (`_SkipRule`):
metacomandos do `psql`, controle de transação e, no dry run, `setval`. Um
`COPY ... FROM stdin` encerra a aplicação. Depois de cada arquivo o aplicador
executa `RESET ALL`, porque antes cada arquivo rodava numa sessão de `psql` e as
configurações do dump morriam com ela.

A `_ContinuePolicy` abre um savepoint por lote. Se um comando falha, volta ao
savepoint, reexecuta o lote comando a comando com um savepoint para cada um e
abre um lote novo no comando seguinte. Isso não mantém a transação abaixo de 64
subtransações: uma subtransação que gravou dados fica na lista até o fim, mesmo
depois do `RELEASE SAVEPOINT`. Uma carga de 1 milhão de comandos em lotes de 1000
cria 1000 subtransações. Com o destino isolado isso é aceitável, mas precisa ser
medido numa carga real antes de usar `continue` num banco com réplica.

Falhas esperadas (comando com erro no `stop`, comando não suportado, aspas sem
fechamento, `COMMIT` recusado) terminam em `ROLLBACK` e devolvem o relatório com
resultado `failed`. Qualquer outra exceção, inclusive Ctrl+C, também faz
`ROLLBACK` e grava o relatório antes de ser relançada.

O uso pela linha de comando, o formato do relatório e os cuidados de operação
estão em [execucao.md](execucao.md#aplicando-os-sqls-no-destino).

## Artefatos gerados

Os artefatos ficam em `runs/<cliente>/<YYYYmmdd-HHMMSS>/`:

- `manifest.json`
- `logs/migration.log`
- `logs/*.sql.log`, gravados pelo `rodar2.sh`
- `erros/*.txt`, gravados pelo `rodar2.sh`
- `sql/00_setup.sql`
- `sql/01_prepare_target.sql`
- `sql/02_dump_compatible_tables.sql`
- `sql/03_load_transformed_data.sql`
- `sql/04_finalize_target.sql`
- `aplicacoes/<YYYYmmdd-HHMMSS>-<apply|dry-run>/relatorio.json` e `erros.txt`, um par por aplicação

Responsabilidade de cada artefato:

- `00_setup.sql`: arquivo inicial da execução.
- `01_prepare_target.sql`: preparação da V2, com desabilitação de triggers/índices e limpeza de dados.
- `02_dump_compatible_tables.sql`: saída do `pg_dump` da V1 para tabelas que podem ser carregadas em bloco.
- `03_load_transformed_data.sql`: carga principal e ajustes intermediários.
- `04_finalize_target.sql`: `setval` de sequences e reativação de índices/triggers.

## Estratégia de carga

O processo mistura dois modelos:

- `pg_dump --column-inserts` para copiar tabelas que podem ser migradas quase como espelho.
- `INSERT INTO ... VALUES ...` gerado em Python para tabelas que exigem mapeamento, filtros, replace, defaults ou correções.

Isso existe porque parte da base V1 não é compatível diretamente com a estrutura da V2.

## Tratamento de incompatibilidades

O script já incorpora decisões de compatibilidade:

- exclusão de tabelas problemáticas no `pg_dump`;
- exclusão de tabelas inexistentes no ambiente do Acme;
- limpeza prévia de tabelas da V2 para evitar `duplicate key`;
- recriação de índices únicos em tabelas fictícias `acme_*`;
- correções posteriores, como prefixo de `document_files.f_file` e ajustes em datas;
- atualização de usernames com encoding inválido em IDs específicos.

## Dependências operacionais

Para funcionar, o ambiente precisa ter:

- `python3`
- `psycopg2`
- `firebirdsql`
- `python-dotenv`
- `pg_dump`
- `psql`, só para aplicar com o `rodar2.sh`
- acesso de rede aos bancos configurados

O `Aplicar.py` sozinho precisa apenas de Python 3, `psycopg2` e da pasta `core/`.

O diretório `runs/` é criado automaticamente na raiz do projeto e ignorado pelo Git.

## Riscos arquiteturais atuais

- O `main` chama `enable_debug(100)`, o que limita as consultas e impede migração completa se isso não for removido.
- A aplicação em transação única bloqueia o destino até o fim. Um erro perto do fim descarta a carga inteira.
- Os `setval` executados antes de um erro continuam valendo depois do `ROLLBACK`, porque sequences não são transacionais.
- O escaping é manual em vários `INSERT`s; isso funciona para o padrão atual, mas é frágil para casos fora da curva.
- A lista de tabelas ignoradas e deletadas é estática e depende do histórico do Acme.
