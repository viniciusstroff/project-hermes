# Project Hermes

CLI em Python que gera os scripts SQL para migrar os dados de um cliente da plataforma V1 para a V2. A origem principal é um PostgreSQL legado, complementado por uma base Firebird. O destino é PostgreSQL.

A geração não grava nada no banco de destino. Cada execução produz um diretório com o SQL de cada fase, um log e um manifesto. A carga vem depois, pelo `Aplicar.py`, que aplica esses arquivos numa única transação e tem um modo de dry run. Com a geração separada da aplicação dá para revisar o SQL antes de tocar no destino, validar contra o banco sem alterá-lo e reaplicar os arquivos sem consultar a origem de novo.

## Como funciona

Cada cliente tem um diretório com o próprio script de migração e o `.env` de conexões. O `Migracao.py` carrega esse script e gera as fases em ordem:

| Arquivo | Conteúdo |
|---|---|
| `sql/00_setup.sql` | Abertura da execução |
| `sql/01_prepare_target.sql` | Limpeza das tabelas do destino e desativação de triggers e índices |
| `sql/02_dump_compatible_tables.sql` | `pg_dump` das tabelas que passam da V1 para a V2 sem transformação |
| `sql/03_load_transformed_data.sql` | `INSERT`s e `UPDATE`s gerados em Python para as tabelas que precisam de mapeamento |
| `sql/04_finalize_target.sql` | Ajuste de sequences e reativação de triggers e índices |

Os arquivos ficam em `runs/<cliente>/<AAAAmmdd-HHMMSS>/`, junto com `manifest.json` e `logs/migration.log`. O diretório `runs/` é ignorado pelo Git.

O núcleo em `core/` trabalha com contratos em vez de drivers. A origem é lida por um `SourceAdapter` e todo SQL gerado passa por um `OutputAdapter`. Hoje existem adapters para PostgreSQL e Firebird. Do lado da saída há o `SqlFileOutput` (o padrão) e o `MirrorOutput`, que repassa os mesmos comandos para mais de um output. Os detalhes estão em [docs/arquitetura.md](docs/arquitetura.md).

## Requisitos

- Python 3.12 (a versão da imagem Docker)
- `pg_dump` compatível com a versão do PostgreSQL da V1 (a imagem instala o cliente 9.6)
- `psql`, só se for aplicar os arquivos pelo `rodar2.sh`
- acesso de rede aos bancos de origem, em geral por túnel SSH
- Docker e Docker Compose, se quiser rodar em container ou subir um destino local

## Configuração

As conexões de cada cliente ficam em `<cliente>/.env`:

```bash
cp Acme/.env.example Acme/.env
```

| Prefixo | Uso |
|---|---|
| `PG_V1_*` | PostgreSQL da V1, fonte dos `SELECT`s e do `pg_dump` |
| `FB_V1_*` | Firebird da V1. Opcional: sem `FB_V1_HOST`, o Hermes não tenta essa conexão |
| `PG_V2_*` | PostgreSQL de destino, lido pelo core para metadados como tabelas e triggers |
| `TARGET_DB_*` | Contrato genérico do destino. Deve espelhar `PG_V2_*` até o core deixar de ler essas chaves |

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
pip install -r requirements.txt

# migração completa: pg_dump e geração do SQL
python3 Migracao.py Acme

# modo debug: no máximo 100 linhas por consulta e sem pg_dump
python3 Migracao.py Acme --debug 100
```

Sem número, `--debug` usa 100 linhas.

### Docker

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
docker exec -it project-hermes python3 Migracao.py Acme
```

O segundo arquivo de compose sobe um PostgreSQL 16 como destino. Dentro da rede `hermes-net` ele responde em `target-db:5432`. No host, aparece na porta definida em `TARGET_DB_PUBLISHED_PORT`. Para usar outro destino basta não incluir esse arquivo e apontar `PG_V2_*` e `TARGET_DB_*` para o banco desejado.

### Aplicando o SQL no destino

```bash
export PGHOST=localhost PGPORT=5432 PGDATABASE=tenant PGUSER=tenant PGPASSWORD=tenant

# dry run: executa tudo e termina em ROLLBACK
python3 Aplicar.py runs/Acme/20261120-153000 --dry-run

# carga: termina em COMMIT, ou em ROLLBACK no primeiro erro
python3 Aplicar.py runs/Acme/20261120-153000
```

O `Aplicar.py` roda os arquivos de `sql/` na ordem das fases, numa única transação. Se algo falha, nada da carga fica no destino. O dry run usa `--on-error continue` por padrão e lista todos os erros de uma vez. Cada aplicação grava `relatorio.json` e `erros.txt` em `aplicacoes/<timestamp>-<modo>/`, dentro da pasta da execução.

Para gerar e aplicar de uma vez no destino das chaves `PG_V2_*`, use `python3 Migracao.py Acme --dry-run` ou `--apply`. Os modos, as políticas de erro e os cuidados estão em [docs/execucao.md](docs/execucao.md#aplicando-os-sqls-no-destino).

O `rodar2.sh` continua disponível para quem aplica com `psql`. Ele roda cada arquivo em autocommit e não para no erro, então uma falha deixa o destino carregado pela metade.

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

Cada cliente é um diretório `<Cliente>/` com um `<Cliente>.py` de mesmo nome, que define a classe `Migracao` herdando de `BaseMigration`. O script declara as tabelas fora do `pg_dump` e a ordem das cargas. As transformações de campo usam as estratégias do core (`Copy`, `Rename`, `Lookup`, `DateConvert` e outras). O passo a passo está em [docs/novo-cliente.md](docs/novo-cliente.md) e o catálogo de estratégias em [docs/estrategias.md](docs/estrategias.md).

Para ver um cliente completo rodando, use a [AcmeEdu](AcmeEdu/README.md), uma instituição de ensino fictícia com V1 e V2 em Docker. Ela gera, aplica e confere a migração de ponta a ponta, com renomeação de tabelas e dados agrupados e separados.

## Estrutura

```text
Migracao.py      ponto de entrada da geração
Aplicar.py       aplica no destino os arquivos de uma execução
Acme/            cliente modelo, sem banco
AcmeEdu/         cliente fictício com V1 e V2 em Docker, para rodar de ponta a ponta
core/            runner, contexto, migrations e estratégias de campo
  adapters/      leitura da origem e renderização para o destino
  apply/         separador de comandos, aplicador e relatório
  dialects/      diferenças de sintaxe entre bancos
  metadata/      triggers, índices, sequences e mapas de referência do destino
  outputs/       para onde vai o SQL gerado
tests/           testes unitários
docs/            documentação técnica
openspec/        specs e changes do fluxo OpenSpec
rodar2.sh        alternativa ao Aplicar.py, com psql
```

## Documentação

- [docs/arquitetura.md](docs/arquitetura.md): componentes, contratos e artefatos
- [docs/fluxo-migracao.md](docs/fluxo-migracao.md): o que acontece em cada etapa da execução
- [docs/execucao.md](docs/execucao.md): passo a passo operacional, do túnel SSH à verificação dos logs
- [docs/novo-cliente.md](docs/novo-cliente.md): checklist para criar a migração de um cliente
- [docs/estrategias.md](docs/estrategias.md): estratégias de transformação de campo
- [docs/melhorias-futuras.md](docs/melhorias-futuras.md): plano de evolução com contexto e riscos de cada item

## Planos futuros

Os contratos de adapters, o `OutputAdapter` (com `SqlFileOutput` e `MirrorOutput`) e a aplicação no destino com dry run já estão implementados. O plano completo, com a justificativa de cada item, está em [docs/melhorias-futuras.md](docs/melhorias-futuras.md).

### Próximo passo

Pela ordem de `melhorias-futuras.md`, o próximo item é levar os demais clientes para o `core/` e reduzir o SQL montado à mão nos scripts de cliente.

O `DirectDatabaseOutput`, que executaria os comandos durante a geração em vez de depois, ficou adiado. Hoje a geração lê o destino no meio da carga para montar mapas de referência. Executar os comandos ao mesmo tempo mudaria o que essa leitura enxerga. Ele volta à fila quando os lookups forem carregados antes da primeira fase (melhoria 7).

### Depois

A ordem segue a recomendada em `melhorias-futuras.md`:

1. Padronizar os lookups de referência e então implementar o `DirectDatabaseOutput`, com a escolha do output pela linha de comando.
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
