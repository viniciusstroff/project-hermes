## Context

A geração tem dois registros:

- `MigrationLogger` grava `[dd/mm/aaaa hh:mm:ss] mensagem` em `logs/migration.log` e no stdout. Não há nível: erros aparecem como mensagens que começam com `[ERRO]`.
- `ArtifactManager.write_manifest()` reescreve o `manifest.json` inteiro com status, limite de debug, caminhos, bancos sem senha e a descrição do output.

O progresso das tabelas passa por `engine.report_progress(label, count, total, done)`. `TableMigration`, `ExpandMigration` e `MultiTargetMigration` chamam esse método no início, a cada `progress_every` linhas e no fim. O `MigrationProgress` usa os números para o rodapé do terminal e para a linha "Progresso geral". Ele zera tudo em `reset()` e não guarda duração por tabela. Na `ExpandMigration`, a contagem é de linhas expandidas, não de linhas lidas da origem.

O `dump_tables()` roda antes do `MigrationRunner`, chamado pelo `Migracao.py` quando a execução não é debug. O `pg_dump` grava o `02_dump_compatible_tables.sql` direto, fora do output.

O aplicador já tem relatório próprio em `aplicacoes/<timestamp>-<modo>/relatorio.json`, um por aplicação.

A change `revisar-estrategias-dependentes-de-dialeto` faz cada strategy devolver só o valor Python e deixa a serialização com o adapter de destino. O `DateConvert` passa a devolver `datetime.date`. A change `padronizar-lookups-de-referencia` cria o `LoadedReferenceMap`, que conhece o próprio nome.

## Goals / Non-Goals

**Goals:**

- Saber, sem abrir o SQL, quantas linhas cada unidade leu, quantos comandos gerou por tabela de destino e quanto tempo levou.
- Saber em que coluna e por que motivo um valor da origem virou `NULL` ou fallback, com exemplos suficientes para corrigir o mapa ou a regra.
- Ter um log que dê para filtrar por fase, unidade e nível sem `grep` em texto livre.
- Manter o relatório da geração separado do relatório de aplicação.

**Non-Goals:**

- Não registrar checkpoints nem permitir retomada (item 13).
- Não medir memória.
- Não comparar origem e destino. No cliente fictício isso é papel do `AcmeEdu/conferir.py`.
- Não mudar o relatório do aplicador.
- Não contar o SQL que o cliente escreve à mão por `write_sql()`, como `TRUNCATE` e `UPDATE` avulsos. Esses comandos não pertencem a uma unidade.
- Não exportar em Markdown ou HTML. O JSON e o resumo no log cobrem o uso atual.

## Decisions

1. Coletor único no contexto, alimentado pelas migrations.

   `core/metrics.py` define `GenerationMetrics`, criado pela `DefaultMigrationContextFactory` e exposto como `context.metrics`. O `MigrationEngine` recebe o coletor junto com o `progress_fn`. Cada migration abre uma unidade no início do `run()` e fecha no fim:

   ```python
   unit = engine.metrics.start_unit(label, source=engine.source_name)
   ...
   unit.row_read()
   unit.statement_written(target)
   ...
   unit.finish()
   ```

   A unidade guarda linhas lidas da origem, comandos por tabela de destino, início, fim, duração e situação (`done` ou `interrupted`). Os contadores ficam em atributos inteiros da unidade, sem gravar nada por linha.

   O coletor fica separado do `MigrationProgress`. O progresso existe para o rodapé do terminal e é zerado a cada `run_inserts()`. As métricas são o registro da execução.

   Alternativa considerada: derivar as métricas do `report_progress`. Rejeitada porque ele não separa linhas lidas de comandos gerados. Na `ExpandMigration`, ele conta linhas expandidas.

2. A unidade pertence à fase aberta.

   O runner informa o coletor a cada `open_phase`. A fase guarda início, fim e duração e soma as unidades que rodaram dentro dela. Em geral todas caem em `load_transformed_data`, mas nada impede uma `TableMigration` dentro do `run_initial()`.

   O `dump_tables()` registra a fase `dump_compatible_tables` com duração, código de saída do `pg_dump` e tamanho do arquivo gerado. Em execução debug a fase não aparece, porque o dump não roda.

3. A strategy informa a perda pelo valor que devolve.

   O `FieldStrategy` ganha `resolve(row)`. A implementação padrão devolve `raw_value(row)`. As strategies que podem perder dado sobrescrevem `resolve` e, quando perdem, devolvem `LostValue(value, reason, sample)`. O `raw_value(row)` dessas strategies continua devolvendo o valor puro.

   | Strategy | Motivo | Quando conta | Amostra |
   |---|---|---|---|
   | `Lookup` | `lookup_miss` | valor da origem não nulo cuja chave normalizada não está no mapa | a chave normalizada |
   | `DateConvert` | `invalid_date` | texto não vazio que não segue `from_fmt` | o texto original |
   | `EmailExtract` | `email_not_found` | texto não vazio sem e-mail | o texto original, truncado |

   Valor nulo ou vazio na origem não é perda: a origem já não tinha o dado. O `Lookup` com `LoadedReferenceMap` inclui o nome do mapa no `LostValue`.

   A migration chama `resolve`, desembrulha o `LostValue`, registra a perda na unidade e serializa o valor pelo adapter de destino, no caminho criado por `revisar-estrategias-dependentes-de-dialeto`. Uma strategy de terceiros sem `resolve` usa `raw_value`, como hoje.

   Alternativas consideradas:

   - Passar um callback de perda para `raw_value(row, report)`. Rejeitada porque muda a assinatura de toda strategy, inclusive as dos clientes.
   - Um coletor global, por `contextvars`, que a strategy chamaria. Rejeitada porque esconde a dependência e complica o teste da strategy isolada.
   - Devolver sempre um objeto com valor e perda. Rejeitada porque cria um objeto por campo e por linha. Com o `LostValue` só nas perdas, o caminho comum ganha apenas um `isinstance`.

4. Amostra limitada por perda.

   A perda é agrupada por unidade, tabela de destino, coluna, strategy e motivo. Cada grupo guarda o total e até 20 valores distintos de amostra, cada um truncado em 100 caracteres. A memória fica limitada mesmo numa tabela de milhões de linhas com o mapa errado.

5. Relatório próprio na raiz da execução.

   O coletor grava `relatorio-geracao.json` na raiz de `runs/<cliente>/<timestamp>/`, com:

   - `run_id` (`<cliente>/<timestamp>`), status, limite de debug, início, fim e duração;
   - fases, na ordem em que abriram, com duração e totais;
   - unidades, com origem, linhas lidas, comandos por destino, duração, situação e perdas;
   - tamanho de cada arquivo em `sql/`, quando o output grava arquivo.

   O runner grava o relatório nos dois caminhos: no sucesso, depois de `finish(True)` e antes do manifesto `success`. Na falha, grava no tratamento de erro, antes do manifesto `failed`. Na falha, a unidade em andamento sai como `interrupted`, com os contadores parciais.

   Alternativa considerada: gravar as métricas no `manifest.json`. Rejeitada porque o manifesto é reescrito inteiro a cada `write_manifest()` e é lido pelo aplicador para validar a execução. As amostras de perda o deixariam grande. O manifesto não muda.

   O nome `relatorio-geracao.json` e o lugar, fora de `aplicacoes/`, evitam confusão com o relatório de cada aplicação. Uma execução tem uma geração e pode ter várias aplicações.

6. Log estruturado ao lado do texto.

   O `MigrationLogger` passa a gravar também `logs/migration.jsonl`, uma linha JSON por evento:

   ```json
   {"ts": "2026-11-25T10:31:02.114", "run_id": "AcmeEdu/20261125-103055", "level": "info", "phase": "load_transformed_data", "unit": "public.students", "event": "unit_finished", "message": "...", "data": {"rows_read": 1500, "statements": {"public.students": 1500}, "duration_seconds": 0.84}}
   ```

   Toda chamada a `print_log()` vira um evento `log`. O coletor emite `phase_started`, `phase_finished`, `unit_started`, `unit_finished` e `data_loss`. A fase e a unidade correntes vêm do coletor.

   O `print_log()` ganha `level='info'` como argumento opcional. Os pontos que hoje escrevem `[ERRO]` passam `level='error'`. No `migration.log` o texto continua igual, para não quebrar quem lê o arquivo.

7. Resumo legível no fim do log.

   Antes de gravar o relatório, o runner escreve no log uma linha por unidade (linhas lidas, comandos, duração) e uma linha `warning` por grupo de perda, com até cinco amostras. Exemplo:

   ```text
   public.students.f_status: 12 valores sem correspondência no mapa student_statuses (ex.: 'X', 'Z')
   ```

   Quem roda o `Migracao.py` vê as perdas no terminal sem abrir o JSON.

## Risks / Trade-offs

- As amostras contêm dados pessoais da origem, como datas de nascimento e trechos de observação. Mitigação: o relatório fica na mesma pasta dos SQL, que já têm todos os dados. O limite de 20 amostras e 100 caracteres reduz o volume. Ver Open Questions.
- Um `isinstance` por campo e por linha deixa a geração mais lenta. Mitigação: medir com o `AcmeEdu` antes e depois. Se o custo passar de alguns por cento, contar a perda só nas strategies que declaram poder perder dado.
- O `migration.jsonl` dobra o volume de escrita do log. O log tem uma linha por evento, não por linha da origem, então o volume é pequeno.
- Strategies de cliente que perdem dado continuam sem contar até implementarem `resolve`. Mitigação: documentar o `LostValue` em `docs/estrategias.md`.
- O `Transform` não sabe distinguir perda de transformação, então não conta. O cliente que quiser contar escreve uma strategy com `resolve`.

## Migration Plan

1. Implementar depois de `revisar-estrategias-dependentes-de-dialeto`.
2. Criar `core/metrics.py` com coletor, unidade, fase, `LostValue` e gravação do relatório, com testes.
3. Ligar o coletor no contexto, no engine e nas três migrations.
4. Implementar `resolve` em `Lookup`, `DateConvert` e `EmailExtract`.
5. Registrar as fases no runner e o dump no `dump_tables()`.
6. Acrescentar o nível e o `migration.jsonl` ao logger.
7. Rodar o `AcmeEdu` completo, conferir o relatório contra o `conferir.py` e medir o tempo de geração.
8. Documentar.

Rollback: reverter os commits. O SQL gerado, o manifesto e o relatório de aplicação não mudam.

## Open Questions

- A amostra de valores deve poder ser desligada por configuração, para clientes com exigência maior de privacidade? Ou o relatório deve guardar só contagens por padrão?
- Vale contar perdas também nas strategies de limpeza (`CpfClean`, `PhoneClean`) quando o resultado fica vazio ou com tamanho inesperado? Hoje elas não trocam o valor por `NULL`, então ficaram de fora.
- O `rodar2.sh` e o `Aplicar.py` devem mostrar o resumo de perdas antes de aplicar? Isso ligaria o relatório da geração à aplicação, o que esta change evitou.
