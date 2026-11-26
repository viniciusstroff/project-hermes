## Context

A geração tem dois registros:

- `MigrationLogger` grava `[dd/mm/aaaa hh:mm:ss] mensagem` em `logs/migration.log` e no stdout. Não há nível: erros aparecem como mensagens que começam com `[ERRO]` e avisos com `[AVISO]`.
- `ArtifactManager.write_manifest()` reescreve o `manifest.json` inteiro com status, limite de debug, artefatos, conexões sem senha e a descrição do output.

O progresso das tabelas passa por `engine.report_progress(label, count, total, done)`. `TableMigration`, `ExpandMigration`, `MultiTargetMigration` e `CopyEntity` chamam esse método no início, a cada `progress_every` linhas e no fim. O `MigrationProgress` usa os números para o rodapé do terminal e para a linha "Progresso geral". Ele zera tudo em `reset()` e não guarda duração por tabela. Na `ExpandMigration`, a contagem é de linhas expandidas, não de linhas lidas da origem.

Cada migration lê as linhas com `engine.read(query)`, pega o valor Python de cada strategy com `raw_value(row)` e emite um `Insert` com `engine.insert(entidade, pares)`. O adapter de destino renderiza o `Insert` no output.

A cópia das entidades compatíveis roda dentro do `MigrationRunner`, na fase `copy_compatible_entities`. A cópia linha a linha (`CopyEntity.run`) passa pelo engine como as outras migrações. A cópia nativa (`pg_dump`) grava um arquivo à parte, que o output inclui na fase com `include_file()`. Um `pg_dump` com erro levanta exceção e a execução falha.

O aplicador já tem relatório próprio em `aplicacoes/<timestamp>-<modo>/relatorio.json`, um por aplicação.

O `LoadedReferenceMap` conhece o próprio nome. O `Lookup` já usa o normalizador de chave dele.

## Goals / Non-Goals

**Goals:**

- Saber, sem abrir os artefatos, quantas linhas cada unidade leu, quantos inserts gerou por entidade de destino e quanto tempo levou.
- Saber em que coluna e por que motivo um valor da origem virou `None` ou fallback, com exemplos suficientes para corrigir o mapa ou a regra.
- Ter um log que dê para filtrar por fase, unidade e nível sem `grep` em texto livre.
- Manter o relatório da geração separado do relatório de aplicação.

**Non-Goals:**

- Não registrar checkpoints nem permitir retomada (item 13).
- Não medir memória.
- Não comparar origem e destino. No cliente fictício isso é papel do `AcmeEdu/conferir.py`.
- Não mudar o relatório do aplicador nem o manifesto.
- Não contar as operações que o cliente emite fora de uma unidade, como `Clear`, `Native` e um `engine.insert()` avulso. Esses comandos não pertencem a uma unidade.
- Não exportar em Markdown ou HTML. O JSON e o resumo no log cobrem o uso atual.

## Decisions

1. Coletor único no contexto, alimentado pelo engine.

   `core/metrics.py` define `GenerationMetrics`, criado pela `DefaultMigrationContextFactory` e exposto como `context.metrics`. O `context.source(nome)` passa ao `MigrationEngine` o coletor e o nome da origem. Cada migration abre uma unidade em volta do `run()`:

   ```python
   with engine.unit(self.target) as unit:
       values = engine.field_values(self.target, self.fields)
       for row in engine.read(query):
           unit.row_read()
           engine.insert(self.target, values(row))
   ```

   O `engine.insert()` conta o insert na unidade aberta, por entidade de destino. O `engine.field_values()` monta, uma vez por execução, a função que transforma uma linha da origem nos pares coluna e valor do `Insert` e registra a perda, se houver (decisão 3). O gerenciador de contexto fecha a unidade como `done` ou, quando uma exceção sai do bloco, como `interrupted`, com os contadores parciais.

   A unidade guarda rótulo, origem, fase, linhas lidas, inserts por entidade, início, fim, duração e situação. Os contadores são inteiros, sem gravar nada por linha. O coletor guarda as unidades abertas numa pilha. O insert vai para a do topo.

   Sem coletor (um engine montado à mão nos testes), `engine.unit()` devolve uma unidade que não registra nada.

   O coletor fica separado do `MigrationProgress`. O progresso existe para o rodapé do terminal e é zerado a cada fase. As métricas são o registro da execução.

   Alternativa considerada: derivar as métricas do `report_progress`. Rejeitada porque ele não separa linhas lidas de inserts gerados. Na `ExpandMigration`, ele conta linhas expandidas.

2. A unidade pertence à fase aberta.

   O contexto ganha `open_phase(fase)`, que abre a fase no output e no coletor. A factory abre `setup` nos dois. O runner passa a abrir as fases pelo contexto. A fase guarda início, fim e duração e soma as unidades que rodaram dentro dela. Em geral todas caem em `load_transformed_data`, mas nada impede uma `TableMigration` dentro do `run_initial()`.

   A fase `copy_compatible_entities` é registrada como as outras. Cada `CopyEntity` copiada linha a linha vira uma unidade. Cada cópia nativa vira um registro da fase, com a origem, a ferramenta, as entidades, a duração e o tamanho do arquivo incluído. Não há código de saída: um `pg_dump` que falha levanta exceção. A mensagem vai para o manifesto e para o relatório como falha da execução. Em execução debug a cópia é sempre linha a linha.

3. A strategy informa a perda pelo valor que devolve.

   O `FieldStrategy` ganha `resolve(row)`. A implementação padrão devolve `raw_value(row)`. As strategies que podem perder dado sobrescrevem `resolve` e devolvem `LostValue(value, reason, sample, reference_map=None)` quando perdem. O `raw_value(row)` dessas strategies continua devolvendo o valor puro.

   | Strategy | Motivo | Quando conta | Amostra |
   |---|---|---|---|
   | `Lookup` | `lookup_miss` | valor da origem não nulo cuja chave normalizada não está no mapa | a chave normalizada |
   | `DateConvert` | `invalid_date` | valor não vazio que não segue `from_fmt` | o texto original |
   | `EmailExtract` | `email_not_found` | texto não vazio sem e-mail | o texto original |

   Valor nulo ou vazio na origem não é perda: a origem já não tinha o dado. O `Lookup` com `LoadedReferenceMap` inclui o nome do mapa no `LostValue`.

   A função do `engine.field_values()` chama `resolve` só nas strategies que o sobrescrevem. Ela desembrulha o `LostValue`, registra a perda na unidade e devolve o valor que vai para o `Insert`. As outras, inclusive uma strategy de cliente sem `resolve` (que cumpre o contrato sem herdar de `FieldStrategy`), são lidas com `raw_value`, como hoje. O `insert_col` de cada campo é lido uma vez por execução, não a cada linha.

   Alternativas consideradas:

   - Passar um callback de perda para `raw_value(row, report)`. Rejeitada porque muda a assinatura de toda strategy, inclusive as dos clientes.
   - Um coletor global, por `contextvars`, que a strategy chamaria. Rejeitada porque esconde a dependência e complica o teste da strategy isolada.
   - Devolver sempre um objeto com valor e perda. Rejeitada porque cria um objeto por campo e por linha. Com o `LostValue` só nas perdas, o caminho comum ganha apenas um `isinstance`.

4. Amostra limitada por perda.

   A perda é agrupada por unidade, entidade de destino, coluna, strategy e motivo. Cada grupo guarda o total e até 20 valores distintos de amostra, cada um truncado em 100 caracteres. A memória fica limitada mesmo numa tabela de milhões de linhas com o mapa errado.

5. Relatório próprio na raiz da execução.

   O coletor grava `relatorio-geracao.json` na raiz de `runs/<cliente>/<timestamp>/`, com:

   - `run_id` (`<cliente>/<timestamp>`), status, limite de debug, início, fim e duração;
   - fases, na ordem em que abriram, com duração, totais e cópias nativas;
   - unidades, com origem, fase, linhas lidas, inserts por entidade, duração, situação e perdas;
   - tamanho de cada artefato listado no manifesto, quando o output grava arquivo.

   O runner grava o relatório nos dois caminhos. No sucesso, depois de `finish(True)` e antes do manifesto `success`. Na falha, depois de `finish(False)` e antes do manifesto `failed`. Na falha, a unidade em andamento sai como `interrupted`, com os contadores parciais. O relatório traz a mensagem do erro.

   Para o tamanho dos artefatos ser o final, o `FileOutput.finish()` passa a descarregar também o arquivo aberto, além do buffer.

   Alternativa considerada: gravar as métricas no `manifest.json`. Rejeitada porque o manifesto é reescrito inteiro a cada `write_manifest()` e é lido pelo aplicador para validar a execução. As amostras de perda o deixariam grande. O manifesto não muda.

   O nome `relatorio-geracao.json` e o lugar, fora de `aplicacoes/`, evitam confusão com o relatório de cada aplicação. Uma execução tem uma geração e pode ter várias aplicações.

6. Log estruturado ao lado do texto.

   O `MigrationLogger` passa a gravar também `logs/migration.jsonl`, uma linha JSON por evento:

   ```json
   {"ts": "2026-11-26T10:31:02.114", "run_id": "AcmeEdu/20261126-103055", "level": "info", "phase": "load_transformed_data", "unit": "students", "event": "unit_finished", "message": "...", "data": {"rows_read": 1500, "inserts": {"students": 1500}, "duration_seconds": 0.84}}
   ```

   Toda chamada a `print_log()` vira um evento `log`. O coletor emite `phase_started`, `phase_finished`, `unit_started`, `unit_finished` e `data_loss`. A fase e a unidade correntes vêm do coletor, que a factory liga ao logger.

   O `print_log()` ganha `level='info'` como argumento opcional. Os pontos do core que hoje escrevem `[ERRO]` passam `level='error'`. O `warn_once()` passa `warning`. No `migration.log` o texto continua igual, com o prefixo, para não quebrar quem lê o arquivo.

7. Resumo legível no fim do log.

   Antes de gravar o relatório, o runner escreve no log uma linha por unidade (linhas lidas, inserts e duração) e uma linha `warning` por grupo de perda, com até cinco amostras. Exemplo:

   ```text
   [AVISO] students.status_id: 12 valores sem correspondência no mapa student_statuses (ex.: 'X', 'Z')
   ```

   Quem roda o `Migracao.py` vê as perdas no terminal sem abrir o JSON.

## Risks / Trade-offs

- As amostras contêm dados pessoais da origem, como datas de nascimento e trechos de observação. Mitigação: o relatório fica na mesma pasta dos artefatos, que já têm todos os dados. O limite de 20 amostras e 100 caracteres reduz o volume. Ver Open Questions.
- As métricas deixam a geração mais lenta. Medido com 200 mil linhas sintéticas e seis campos, três deles com perda possível e um terço a metade das linhas perdendo valor. Com o `Insert` renderizado pelo adapter PostgreSQL, como na geração real, a geração ficou 7% a 10% mais lenta. No laço sem renderização, uns 35%. O custo vem de contar linha e insert na unidade (cerca de 0,5 µs por linha) e de registrar cada valor perdido. Campos sem perda possível ficaram mais rápidos que antes, porque o `insert_col` não é mais lido a cada linha. Na `AcmeEdu`, a geração leva menos de um segundo nas duas versões.
- O `migration.jsonl` dobra o volume de escrita do log. O log tem uma linha por evento, não por linha da origem, então o volume é pequeno.
- Strategies de cliente que perdem dado continuam sem contar até implementarem `resolve`. Mitigação: documentar o `LostValue` em `docs/estrategias.md`.
- O `Transform` não sabe distinguir perda de transformação, então não conta. O cliente que quiser contar escreve uma strategy com `resolve`.

## Migration Plan

1. Criar `core/metrics.py` com coletor, unidade, fase, `LostValue` e gravação do relatório, com testes.
2. Ligar o coletor no contexto, no engine e nas migrations, com `engine.unit()` e `engine.field_values()`.
3. Implementar `resolve` em `Lookup`, `DateConvert` e `EmailExtract`.
4. Abrir as fases pelo contexto e registrar a cópia nativa.
5. Acrescentar o nível e o `migration.jsonl` ao logger.
6. Rodar a `AcmeEdu` completa, conferir o relatório contra o `conferir.py` e medir o tempo de geração.
7. Documentar.

Rollback: reverter os commits. Os artefatos gerados, o manifesto e o relatório de aplicação não mudam.

## Open Questions

- A amostra de valores deve poder ser desligada por configuração, para clientes com exigência maior de privacidade? Ou o relatório deve guardar só contagens por padrão?
- Vale contar perdas também nas strategies de limpeza (`CpfClean`, `PhoneClean`) quando o resultado fica vazio ou com tamanho inesperado? Hoje elas não trocam o valor por `None`, então ficaram de fora.
- O `Aplicar.py` deve mostrar o resumo de perdas antes de aplicar? Isso ligaria o relatório da geração à aplicação, o que esta change evitou.
