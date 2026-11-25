## Why

O único cliente do repositório é o `Acme`, um modelo com duas tabelas que não tem banco de origem nem de destino. Não dá para rodar a migração de ponta a ponta sem acesso a uma V1 real. As validações pendentes das changes `aplicar-artefatos-sql-no-destino` (task 6.4) e `preservar-barra-invertida-nos-literais` (task 3.2) dependem disso. Os clientes reais ficam em repositório privado e não podem servir de exemplo nem de teste.

Um cliente fictício com V1 e V2 em Docker, dados gerados e uma estrutura nova de verdade (campos renomeados, tipos padronizados, tabelas divididas) permite exercitar o core inteiro sem dado de cliente: geração, `pg_dump`, aplicação, dry run e conferência do resultado.

## What Changes

- Novo cliente fictício `AcmeEdu`, uma instituição de ensino:
  - `AcmeEdu/banco/`: esquema e dados da V1 legada (PostgreSQL 9.6) e esquema da V2 (PostgreSQL 16);
  - `AcmeEdu/docker-compose.yml`: sobe as duas bases na rede `hermes-net`;
  - `AcmeEdu/AcmeEdu.py`: migração V1 → V2 usando `TableMigration`, `MultiTargetMigration`, strategies, mapa de referência carregado da V2, índices e sequences;
  - `AcmeEdu/conferir.py`: compara V1 e V2 depois da carga e diz o que está diferente.
- A V1 tem os problemas típicos de uma base legada: nomes em maiúsculas com espaços sobrando, CPF e telefone em formatos variados, datas em texto, e-mail no meio de outro texto, códigos de uma letra e texto com aspas, `;` e barra invertida.
- A V2 usa nomes em inglês sem prefixo (`id`, `name`, `birth_date`), tipos próprios (`date`, `boolean`, `numeric`), tabelas renomeadas, uma tabela de domínio nova e dados agrupados e separados: alunos e professores viram uma tabela `people` única, sem duplicar quem é as duas coisas. Telefones e responsáveis saem para tabelas próprias.
- **Core:** a conexão com o Firebird passa a ser opcional. Sem `FB_V1_HOST` no `.env`, o contexto não tenta conectar e não registra a origem `fb_v1`.
- Documentação do cliente fictício e do passo a passo para rodar a migração completa em Docker.

## Capabilities

### New Capabilities

- `reference-client`: cliente fictício versionado, com bases de origem e destino reproduzíveis em Docker, que roda a migração completa e confere o resultado.

### Modified Capabilities

- `adapter-agnostic-migration-core`: o contexto padrão deixa de exigir a conexão com o Firebird quando ela não está configurada.

## Impact

- Novos arquivos em `AcmeEdu/` e testes das transformações do cliente.
- Código afetado: `core/context.py` (Firebird opcional).
- Imagem `postgres:9.6-alpine` passa a ser usada pelo compose do cliente fictício, para combinar com o `pg_dump` 9.6 da imagem do Hermes.
- Destrava as tasks 6.4 e 3.2 das changes anteriores, que podem ser feitas com uma execução gerada a partir do `AcmeEdu`.
- Nenhum dado real de cliente entra no repositório.
