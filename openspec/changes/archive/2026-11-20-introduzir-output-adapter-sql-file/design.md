## Context

A arquitetura atual já possui `MigrationContext`, `MigrationRunner` e um writer de artefatos SQL. Esse writer ainda é uma dependência concreta do contexto e do runner: abrir fase, escrever SQL, imprimir comentários e fechar arquivo são tratados como comportamento fixo de arquivo.

O item 2 do roadmap propõe separar “gerar comandos” de “como entregar esses comandos”. Esta change implementa apenas a primeira etapa: tornar a saída atual um adapter explícito, sem mudar a execução padrão nem adicionar execução direta no banco.

## Goals / Non-Goals

**Goals:**

- Definir um contrato `OutputAdapter` para `open_phase(phase)`, `write(sql)`, `comment(text)`, `flush()` e `close()`.
- Implementar `SqlFileOutput` preservando o comportamento atual de arquivos SQL e buffer.
- Fazer `MigrationContext`, `MigrationRunner` e `BaseMigration` dependerem do contrato de output.
- Manter os helpers de escrita da fachada `BaseMigration` (`write_sql()`, `print_comment()`, `_open_sql_file()`) delegando para o output.
- Remover `context.writer` e `SqlArtifactWriter`, deixando `context.output` como única API de saída no contexto.
- Adicionar testes que provem equivalência dos artefatos atuais.

**Non-Goals:**

- Não implementar `DirectDatabaseOutput`, `MirrorOutput`, `StdoutOutput` ou execução híbrida.
- Não mudar CLI, `.env`, nomes de arquivos, manifesto ou ordem das fases.
- Não converter SQL bruto em operações estruturadas.
- Não alterar a estratégia de transação ou rollback operacional.

## Decisions

1. Usar um contrato pequeno de output.

   `OutputAdapter` cobre apenas o que o runtime já usa: abrir fase, escrever SQL, escrever comentário, flush e close. Alternativa considerada: modelar operações estruturadas como `InsertOperation`, `AlterTableOperation` e `SequenceResetOperation`. Rejeitada nesta etapa porque mudaria o contrato das migrations e ampliaria o escopo antes de estabilizar a fronteira de saída.

2. Renomear conceitualmente o writer para `SqlFileOutput`.

   O comportamento atual de `SqlArtifactWriter` vira a implementação concreta padrão em `core/outputs/sql_file.py`. O nome `SqlArtifactWriter` é removido, assim como os nomes antigos de método (`write_sql`, `print_comment`, `open_sql_file`) no output.

3. Remover `context.writer`.

   O contexto expõe só `context.output`. Alternativa considerada: manter `context.writer` como alias transitório. Rejeitada porque o projeto não está em produção e não há Acme externo dependendo do nome antigo, então o alias só manteria duas formas de fazer a mesma coisa. `BaseMigration.write_sql()`, `print_comment()` e `_open_sql_file()` continuam porque são a API da fachada por herança usada pelos Acmes. Eles passam a delegar para `context.output`.

4. Manter fases nomeadas por chave lógica.

   O output recebe chaves como `setup`, `prepare_target`, `load_transformed_data` e `finalize_target`, mantendo o mapeamento de nomes de arquivo em `ArtifactManager`. Alternativa considerada: passar caminhos absolutos para o output. Rejeitada porque espalharia conhecimento dos artefatos fora do adapter.

5. Adiar outputs executáveis.

   `DirectDatabaseOutput` exige transações, erro parcial, idempotência, observabilidade e rollback. Esta change prepara a extensão, mas só entrega `SqlFileOutput` para manter risco baixo.

## Risks / Trade-offs

- `OutputAdapter` textual limitar futuros outputs diretos -> mitigar aceitando que esta é a fronteira v1; operações estruturadas podem ser uma change posterior.
- Remoção de `context.writer` e `SqlArtifactWriter` quebrar código composto -> aceito, porque não há execução em produção. Testes e docs passam a usar `context.output`; Acmes por herança continuam com os helpers de `BaseMigration`.
- Runner continuar conhecendo fases fixas -> aceitável nesta etapa; o objetivo é desacoplar entrega, não tornar o workflow configurável.

## Migration Plan

1. Criar `core/outputs/` com `OutputAdapter` e `SqlFileOutput`.
2. Reexportar os novos tipos pelo pacote `core`.
3. Atualizar `MigrationContext` para receber `output` e remover `writer`.
4. Atualizar `DefaultMigrationContextFactory` para construir `SqlFileOutput`.
5. Atualizar `MigrationRunner` e `BaseMigration` para usar métodos de output.
6. Remover `core.runtime.SqlArtifactWriter`.
7. Atualizar documentação do roadmap/arquitetura.
8. Adicionar testes de contrato e regressão de artefatos.

Rollback: como o comportamento externo permanece igual, o rollback é reverter esta change e voltar ao writer concreto anterior. Não há migração de dados persistente.

## Open Questions

Respondidas durante a implementação:

- `context.writer` foi removido nesta change, já que o projeto não está em produção.
- A próxima change de output é `MirrorOutput`. `DirectDatabaseOutput` vem depois e deve considerar um modo de dry run (avaliação em `docs/melhorias-futuras.md`, item 2).

Em aberto:

- `close()` não informa se a execução terminou com sucesso. Um output que executa no banco precisa dessa informação para decidir entre commit e rollback, então o contrato deve ganhar esse sinal antes de `DirectDatabaseOutput`.
