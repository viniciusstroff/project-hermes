# Project Hermes

CLI em Python que gera os scripts SQL para migrar os dados de um cliente da plataforma V1 para a V2. A origem principal é um PostgreSQL legado, complementado por uma base Firebird. O destino é PostgreSQL.

O Hermes não grava nada no banco de destino. Cada execução produz um diretório com o SQL de cada fase, um log e um manifesto. A carga é feita depois com `psql`, normalmente pelo `rodar2.sh`. Com a geração separada da aplicação dá para revisar o SQL antes de tocar no destino e reaplicar os arquivos sem consultar a origem de novo.

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
- `psql` para aplicar os arquivos no destino
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
| `FB_V1_*` | Firebird da V1. A conexão é sempre tentada, então preencha mesmo que o cliente não use |
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
RUN_DIR=runs/Acme/20261120-153000 ./rodar2.sh
```

O script lê a conexão de `PGHOST`, `PGPORT`, `PGDATABASE` e `PGUSER` (padrão `localhost:4003`, base e usuário `tenant-demo`). Os logs do `psql` vão para `$RUN_DIR/logs/` e os erros com contexto para `$RUN_DIR/erros/`. Se não houve erro, os arquivos de `erros/` ficam vazios.

Cada arquivo roda em autocommit e um erro não interrompe a carga. Quando algo falha, o destino fica carregado pela metade. Antes de reexecutar, recrie a base a partir do dump de homologação, como descrito em [docs/execucao.md](docs/execucao.md#resetar-o-banco-v2-local-antes-de-reexecutar).

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

## Estrutura

```text
Migracao.py      ponto de entrada da CLI
Acme/            cliente de exemplo
core/            runner, contexto, migrations e estratégias de campo
  adapters/      leitura da origem e renderização para o destino
  dialects/      diferenças de sintaxe entre bancos
  metadata/      triggers, índices, sequences e mapas de referência do destino
  outputs/       para onde vai o SQL gerado
common/          utilitários de data e regex
tests/           testes unitários
docs/            documentação técnica
openspec/        specs e changes do fluxo OpenSpec
rodar2.sh        aplica no destino os arquivos de uma execução
```

## Documentação

- [docs/arquitetura.md](docs/arquitetura.md): componentes, contratos e artefatos
- [docs/fluxo-migracao.md](docs/fluxo-migracao.md): o que acontece em cada etapa da execução
- [docs/execucao.md](docs/execucao.md): passo a passo operacional, do túnel SSH à verificação dos logs
- [docs/novo-cliente.md](docs/novo-cliente.md): checklist para criar a migração de um cliente
- [docs/estrategias.md](docs/estrategias.md): estratégias de transformação de campo
- [docs/melhorias-futuras.md](docs/melhorias-futuras.md): plano de evolução com contexto e riscos de cada item

## Planos futuros

Os contratos de adapters e o `OutputAdapter`, com `SqlFileOutput` e `MirrorOutput`, já estão implementados. O plano completo, com a justificativa de cada item, está em [docs/melhorias-futuras.md](docs/melhorias-futuras.md).

### Próximo passo

O próximo output é o `DirectDatabaseOutput`, que executa os comandos direto no destino com commit por lote. Ele terá um modo de dry run que roda todas as fases numa transação e termina em `ROLLBACK`. A ideia é ver os erros antes da carga real, em vez de descobrir com o destino já carregado pela metade. Combinado com o `MirrorOutput`, uma única execução grava os arquivos para auditoria e valida contra o banco.

Junto vem a escolha do output pela linha de comando. A sintaxe abaixo é proposta e ainda não existe:

```bash
python3 Migracao.py Acme --output direct-db --dry-run
```

### Depois

A ordem segue a recomendada em `melhorias-futuras.md`:

1. Levar os demais clientes para o `core/` e reduzir o SQL montado à mão nos scripts de cliente.
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
