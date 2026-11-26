## Why

Um valor de texto com `\r\n` ou `\r` sai certo no artefato, mas chega ao destino só com `\n`. O `FileOutput` grava o `\r` como está. Quem lê o artefato depois o abre com a tradução de quebra de linha padrão do Python, que troca `\r\n` e `\r` por `\n` antes de o separador ver o texto:

- o `ArtifactApplier`, ao aplicar cada artefato;
- o `split_sql_file()`;
- o `include_file()` do `FileOutput`, que copia a saída do `pg_dump` para a fase;
- o `include_file()` do `DirectDatabaseOutput`.

A carga termina sem erro e o texto muda no destino. O caso foi reproduzido com o valor `'linha 1\r\nlinha 2'`: o `INSERT` no arquivo tem os bytes `\r\n`. O comando que o separador entrega tem só `\n`.

O defeito atinge os valores gerados pelo core e a saída do `pg_dump`, que grava os literais sem escapar o `\r`. Ele também atingiria o paralelismo proposto em `paralelizar-geracao-de-tarefas-independentes`, que junta segmentos com `include_file()`. O MongoDB não é atingido, porque o JSON escapa o `\r`.

## What Changes

- Os artefatos passam a ser gravados e lidos sem tradução de quebra de linha (`newline=''`): o arquivo de cada fase no `FileOutput`, a cópia do `include_file()` nos dois outputs, a leitura do `ArtifactApplier` e o `split_sql_file()`.
- Um comentário `--` continua terminando no `\n` ou no `\r`, como no lexer do PostgreSQL. Um `\r` dentro de um literal fica no literal.
- Testes com `\r\n`, `\r` sozinho e `\n` dentro de valores de texto, do output até o destino.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `configurable-migration-output`: o output grava os artefatos e inclui arquivos sem mudar as quebras de linha.
- `sql-artifact-application`: o aplicador lê os artefatos sem mudar as quebras de linha.
- `direct-database-output`: o output direto lê o arquivo incluído sem mudar as quebras de linha.

## Impact

- Código afetado: `core/outputs/file.py`, `core/outputs/direct.py`, `core/apply/applier.py` e `core/adapters/postgres_splitter.py`.
- Os arquivos gerados não mudam: o `FileOutput` já grava o `\r`. Muda o que chega ao destino.
- Uma carga já aplicada com textos que tinham `\r` gravou `\n` no lugar. Aplicar de novo os mesmos arquivos, depois desta change, grava o texto certo.
- É pré-requisito de `paralelizar-geracao-de-tarefas-independentes`.
