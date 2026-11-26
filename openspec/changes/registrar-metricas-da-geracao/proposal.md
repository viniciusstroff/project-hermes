## Why

Uma execução da geração deixa o `logs/migration.log` em texto livre. O `manifest.json` guarda status e configuração. Nenhum dos dois diz quantas linhas cada tabela leu da origem, quantos inserts gerou ou quanto tempo levou. Para saber isso hoje é preciso contar linhas no `03_load_transformed_data` ou cronometrar pelo horário das linhas do log.

O problema maior é a perda de dado silenciosa. Três strategies trocam o valor da origem por `None` ou por um fallback sem registrar nada:

- `Lookup` devolve o `fallback` quando a chave não está no mapa;
- `DateConvert` devolve `None` para data impossível, como `31/02/2001`;
- `EmailExtract` devolve `None` quando o texto não tem e-mail.

A carga termina com sucesso e ninguém fica sabendo que 300 alunos ficaram sem situação porque o mapa não tinha a letra `X`. A change `padronizar-lookups-de-referencia` deixou essa contagem para cá.

## What Changes

- Métricas por unidade de migração (`TableMigration`, `ExpandMigration`, `MultiTargetMigration` e cada `CopyEntity` copiada linha a linha): linhas lidas da origem, inserts gerados por entidade de destino, duração e situação final.
- Resumo por fase, com duração e totais das unidades que rodaram nela. A cópia nativa (`pg_dump`) entra na fase `copy_compatible_entities` com a origem, as entidades, a duração e o tamanho do arquivo incluído.
- Contagem de perda de dado por unidade, entidade de destino, coluna, strategy e motivo, com uma amostra limitada de valores originais. As strategies informam a perda pelo valor que devolvem, sem acesso a log ou engine.
- Relatório da geração em `relatorio-geracao.json`, na raiz da pasta da execução, gravado no sucesso e na falha.
- Log estruturado `logs/migration.jsonl` ao lado do `migration.log`, com o identificador da execução, nível, fase e unidade em cada linha.
- Resumo legível no fim do `migration.log`: uma linha por unidade e uma linha de aviso para cada grupo de perda.
- O `MigrationLogger.print_log()` ganha nível (`info`, `warning`, `error`). O texto do `migration.log` continua no formato atual.
- Atualização de `docs/arquitetura.md`, `docs/execucao.md`, `docs/estrategias.md` e do item 14 de `docs/melhorias-futuras.md`.

## Capabilities

### New Capabilities

- `generation-metrics`: métricas da geração por unidade e por fase, contagem de perda de dado nas strategies, relatório da geração e log estruturado.

### Modified Capabilities

- None. O manifesto, os artefatos em `comandos/` e o relatório de aplicação em `aplicacoes/` não mudam.

## Impact

- Novo módulo `core/metrics.py`.
- Código afetado: `core/runtime.py`, `core/context.py`, `core/runner.py`, `core/migration_engine.py`, `core/table_migration.py`, `core/expand_migration.py`, `core/multi_target_migration.py`, `core/strategies.py`, `core/compatible.py` e `core/outputs/file.py`.
- As duas changes de que esta depende já estão na main. A `revisar-estrategias-dependentes-de-dialeto` fez as strategies devolverem valores Python, que a migration põe num `Insert`. A `padronizar-lookups-de-referencia` criou o `LoadedReferenceMap`, que conhece o próprio nome.
- As amostras de perda contêm valores da origem, como datas de nascimento e trechos de texto livre. O relatório tem a mesma sensibilidade dos artefatos da mesma pasta.
