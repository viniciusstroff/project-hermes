# Project Hermes

CLI em Python que gera os scripts SQL para migrar os dados de um cliente da plataforma V1 para a V2. A origem principal é um PostgreSQL legado, complementado por uma base Firebird. O destino é PostgreSQL.

A geração não grava nada no banco de destino. Cada execução produz um diretório com o SQL de cada fase, um log e um manifesto. A carga vem depois, pelo `Aplicar.py`, que aplica esses arquivos numa única transação e tem um modo de dry run. Com a geração separada da aplicação dá para revisar o SQL antes de tocar no destino, validar contra o banco sem alterá-lo e reaplicar os arquivos sem consultar a origem de novo.

## Como funciona

Cada cliente tem um diretório com o próprio script de migração e o `.env` de conexões. O `Migracao.py` carrega esse script e gera as fases em ordem:

| Arquivo | Conteúdo |
|---|---|
| `comandos/00_setup.sql` | Abertura da execução |
| `comandos/01_prepare_target.sql` | Limpeza das tabelas do destino e desativação de triggers |
| `comandos/02_copy_compatible_entities.sql` | Cópia das tabelas que passam da V1 para a V2 sem transformação, listadas em `compatible_entities()` |
| `comandos/03_load_transformed_data.sql` | `INSERT`s gerados em Python para as tabelas que precisam de mapeamento |
| `comandos/04_finalize_target.sql` | Ajuste de sequences e reativação de triggers |

Os arquivos ficam em `runs/<cliente>/<AAAAmmdd-HHMMSS>/`, junto com `manifest.json` e `logs/migration.log`. A extensão vem do adapter de destino: `.sql` no PostgreSQL. O manifesto lista cada artefato com fase, caminho e formato. Ele também registra a versão de cada servidor. O diretório `runs/` é ignorado pelo Git.

O núcleo em `core/` trabalha com contratos em vez de drivers. A origem é lida por um `SourceAdapter` com consultas neutras. A migração emite operações (`Insert`, `Clear`, `SuspendConstraints`, `ResetIdentity`...). O adapter de destino traduz cada uma para o formato dele. Hoje existem adapters para PostgreSQL, Firebird e MongoDB. Do lado da saída há o `FileOutput` (o padrão) e o `MirrorOutput`, que repassa as mesmas operações para mais de um output. Os detalhes estão em [docs/arquitetura.md](docs/arquitetura.md).

## Requisitos

- Python 3.12 (a versão da imagem Docker)
- `pg_dump` da versão principal do PostgreSQL da V1 ou mais novo, só para quem liga a cópia nativa (`SOURCE_<NOME>_NATIVE_COPY=pg_dump`). A imagem só instala o cliente com o argumento de build `PG_CLIENT_VERSION`
- servidores dentro da faixa de cada adapter: PostgreSQL 9.6 ou mais novo como origem e 12 ou mais novo como destino, Firebird 2.5 ou mais novo como origem
- acesso de rede aos bancos de origem, em geral por túnel SSH
- Docker e Docker Compose, se quiser rodar em container ou subir um destino local

## Configuração

As conexões de cada cliente ficam em `<cliente>/.env`:

```bash
cp Acme/.env.example Acme/.env
```

Cada conexão tem nome e adapter. Uma origem é um grupo `SOURCE_<NOME>_*` com a chave `SOURCE_<NOME>_ADAPTER`. O destino usa `TARGET_ADAPTER` e as demais chaves `TARGET_*`. O resto do nome de cada chave, em minúsculas, vira parâmetro do adapter:

```bash
SOURCE_V1_ADAPTER=postgres
SOURCE_V1_HOST=acmeedu-v1
SOURCE_V1_PORT=5432
SOURCE_V1_NAME=acmeedu_v1
SOURCE_V1_USER=acmeedu
SOURCE_V1_PASS=acmeedu

TARGET_ADAPTER=postgres
TARGET_HOST=target-db
TARGET_PORT=5432
TARGET_NAME=tenant
TARGET_USER=tenant
TARGET_PASS=tenant
```

A migração pede a origem pelo nome: `self.context.source('v1')`. Os adapters embutidos são `postgres` (origem e destino), `firebird` (só origem) e `mongodb` (origem e destino). O PostgreSQL e o Firebird exigem `HOST`, `NAME` e `USER`. O MongoDB exige `URI`, que fica fora do manifesto. O banco vem de `NAME` ou da própria URI. Um adapter de fora do core entra pelo caminho `pacote.modulo:Classe` no lugar do nome. O driver de cada banco só é importado quando uma conexão daquele adapter abre, então um cliente só com PostgreSQL não precisa do `firebirdsql`.

O `.env` da raiz (copiado de `.env.example`) só é lido pelo Docker Compose. Nele fica `TARGET_DB_PUBLISHED_PORT`, a porta em que o PostgreSQL local aparece no host.

Quando a V1 está em servidor remoto, abra os túneis antes de rodar:

```bash
ssh -L 172.17.0.1:3050:<ip-firebird>:3050 \
    -L 172.17.0.1:3396:<ip-postgres>:5432 \
    usuario@servidor
```

## Execução

### Local

```bash
# core e os drivers dos adapters que o cliente usa
pip install -r requirements.txt -r requirements/postgres.txt -r requirements/firebird.txt -r requirements/mongodb.txt

# migração completa
python3 Migracao.py Acme

# modo debug: no máximo 100 linhas por consulta, inclusive na cópia das entidades compatíveis
python3 Migracao.py Acme --debug 100
```

Sem número, `--debug` usa 100 linhas.

### Docker

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
docker exec -it project-hermes python3 Migracao.py Acme
```

A imagem instala os drivers de todos os adapters e não traz `pg_dump`. Para a cópia nativa, construa com o cliente da versão que precisar, por exemplo `PG_CLIENT_VERSION=17 docker compose build`, ou ponha `PG_CLIENT_VERSION` no `.env` da raiz.

O segundo arquivo de compose sobe um PostgreSQL 16 como destino. Dentro da rede `hermes-net` ele responde em `target-db:5432`. No host, aparece na porta definida em `TARGET_DB_PUBLISHED_PORT`. Para usar outro destino basta não incluir esse arquivo e apontar as chaves `TARGET_*` do `.env` do cliente para o banco desejado.

### Aplicando o SQL no destino

```bash
export TARGET_HOST=localhost TARGET_PORT=5432 TARGET_NAME=tenant TARGET_USER=tenant TARGET_PASS=tenant

# dry run: executa tudo e termina em ROLLBACK
python3 Aplicar.py runs/Acme/20261120-153000 --dry-run

# carga: termina em COMMIT, ou em ROLLBACK no primeiro erro
python3 Aplicar.py runs/Acme/20261120-153000
```

O `Aplicar.py` lê do `manifest.json` o adapter de destino e a lista de artefatos e roda os comandos na ordem das fases, numa única transação. A conexão vem das variáveis `TARGET_*`, com os mesmos nomes do `.env` do cliente. Se algo falha, nada da carga fica no destino. Quando o destino tem savepoint, o dry run usa `--on-error continue` por padrão e lista todos os erros de uma vez. Cada aplicação grava `relatorio.json` e `erros.txt` em `aplicacoes/<timestamp>-<modo>/`, dentro da pasta da execução.

Para gerar e aplicar de uma vez no destino das chaves `TARGET_*`, use `python3 Migracao.py Acme --dry-run` ou `--apply`. Os modos, as políticas de erro e os cuidados estão em [docs/execucao.md](docs/execucao.md#aplicando-os-sqls-no-destino).

### Depuração

```bash
docker exec -it project-hermes python3 -m debugpy --listen 0.0.0.0:5678 --wait-for-client Migracao.py Acme
```

Depois anexe o depurador do VS Code na porta 5678 com a configuração `Attach Python in Docker`.

## Testes

```bash
python3 -m unittest discover -s tests
```

Os testes trocam `psycopg2` e `firebirdsql` por módulos falsos. Não precisam de banco nem dos drivers instalados.

## Adicionando um cliente

Cada cliente é um diretório `<Cliente>/` com um `<Cliente>.py` de mesmo nome, que define a classe `Migracao` herdando de `BaseMigration`. O script lista as tabelas que passam sem transformação (`compatible_entities()`) e a ordem das cargas. As transformações de campo usam as estratégias do core (`Copy`, `Rename`, `Lookup`, `DateConvert` e outras). O passo a passo está em [docs/novo-cliente.md](docs/novo-cliente.md) e o catálogo de estratégias em [docs/estrategias.md](docs/estrategias.md).

Para ver um cliente completo rodando, use a [AcmeEdu](AcmeEdu/README.md), uma instituição de ensino fictícia com V1 e V2 em Docker. Ela gera, aplica e confere a migração de ponta a ponta, com renomeação de tabelas e dados agrupados e separados.

## Estrutura

```text
Migracao.py      ponto de entrada da geração
Aplicar.py       aplica no destino os arquivos de uma execução
Acme/            cliente modelo, sem banco
AcmeEdu/         cliente fictício com V1 e V2 em Docker, para rodar de ponta a ponta
core/            runner, contexto, migrations e estratégias de campo
  adapters/      leitura da origem e renderização para o destino
  apply/         aplicador e relatório
  dialects/      diferenças de sintaxe entre bancos
  outputs/       para onde vão as operações geradas
tests/           testes unitários
docs/            documentação técnica
openspec/        specs e changes do fluxo OpenSpec
```

## Documentação

- [docs/arquitetura.md](docs/arquitetura.md): componentes, contratos e artefatos
- [docs/fluxo-migracao.md](docs/fluxo-migracao.md): o que acontece em cada etapa da execução
- [docs/execucao.md](docs/execucao.md): passo a passo operacional, do túnel SSH à verificação dos logs
- [docs/novo-cliente.md](docs/novo-cliente.md): checklist para criar a migração de um cliente
- [docs/estrategias.md](docs/estrategias.md): estratégias de transformação de campo
- [docs/conversao-clientes.md](docs/conversao-clientes.md): como levar um cliente do `.env` e da API antigos para o core desacoplado
- [docs/melhorias-futuras.md](docs/melhorias-futuras.md): plano de evolução com contexto e riscos de cada item

## Planos futuros

O core já não depende de um banco nem de uma versão: a change `desacoplar-migracao-de-banco-e-versao` trouxe conexões nomeadas, consultas neutras, escrita por operações, artefatos no formato do destino, aplicação guiada por capacidades e os adapters de PostgreSQL, Firebird e MongoDB. A AcmeEdu roda sem SQL próprio em seis caminhos de banco e versão. O plano completo, com a justificativa de cada item, está em [docs/melhorias-futuras.md](docs/melhorias-futuras.md).

### Próximo passo

O `DirectDatabaseOutput`, que executaria os comandos durante a geração, deixou de esbarrar na leitura do destino no meio da carga. A change `introduzir-direct-database-output` precisa ser reescrita sobre a `TargetSession` e as operações antes de ser implementada.

### Depois

A ordem segue a recomendada em `melhorias-futuras.md`:

1. Implementar o `DirectDatabaseOutput`, revisto para o modelo de operações, com a escolha do output pela linha de comando.
2. Definir um formato declarativo de plano de migração em YAML ou JSON, interpretado pelo mesmo core.
3. Criar um `ExecutionAdapter` que separe a orquestração da execução pesada.
4. Logs estruturados em JSON Lines e relatório por execução com contagem de linhas e tempo por tabela.
5. Checkpoints por fase, tabela e lote, para retomar uma execução longa depois de uma falha.
6. Executor `bulk-copy` com `COPY` e tabelas de staging, antes de considerar outra linguagem.
7. Paralelismo entre tabelas independentes e leitura em streaming, só depois de existirem checkpoints e métricas.
8. Novos adapters de origem e destino quando houver demanda real.
9. Interface gráfica para montar migrações simples sem editar Python, usando o plano declarativo como contrato.
10. Executor externo em Go ou Rust, apenas se medições mostrarem um gargalo que o Python e os recursos nativos do banco não resolvem.
11. Captura de mudanças com Debezium para clientes com janela de parada curta.

## Por que "Hermes"

Na mitologia grega, Hermes é o mensageiro que passa de um mundo a outro e entrega intacto o que leva. O projeto faz isso com os dados de cada cliente, da V1 para a V2.
