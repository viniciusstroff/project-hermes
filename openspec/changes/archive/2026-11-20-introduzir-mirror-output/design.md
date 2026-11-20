## Context

A change `introduzir-output-adapter-sql-file` criou o contrato `OutputAdapter` com `open_phase`, `write`, `comment`, `flush` e `close`, e deixou o `SqlFileOutput` como única implementação. Ela registrou uma questão em aberto: `close()` não informa se a execução terminou com sucesso.

Hoje o `MigrationRunner` captura a exceção e grava `failed` no manifesto, mas o output não recebe esse resultado. O `close()` acontece em `MigrationContext.close()`, que no caminho por herança roda a partir de `BaseMigration.__del__`, ou seja, quando o coletor de lixo decidir. Também roda no caminho de erro da `DefaultMigrationContextFactory`, antes de existir qualquer execução.

O item 2 de `docs/melhorias-futuras.md` define a Fase 2 da saída: `MirrorOutput` e o resultado da execução no contrato. Também pede que o manifesto registre qual output foi usado, para que uma execução validada em dry run não seja confundida com uma aplicada.

## Goals / Non-Goals

**Goals:**

- Dar ao output um sinal explícito de sucesso ou falha da execução.
- Permitir que uma execução entregue os mesmos comandos para mais de um output.
- Registrar no manifesto qual output, ou combinação de outputs, recebeu os comandos.
- Manter os artefatos SQL do caminho padrão byte a byte iguais.

**Non-Goals:**

- Não implementar `DirectDatabaseOutput`, `StdoutOutput` ou dry run.
- Não deixar a factory ou o CLI escolherem o output. Com um único output concreto ainda não há o que escolher.
- Não mudar a ordem das fases nem o conteúdo dos arquivos SQL.
- Não tratar a fase `02_dump_compatible_tables.sql`, que continua sendo gravada direto pelo `pg_dump`.

## Decisions

1. Criar `finish(success: bool)` em vez de mudar a assinatura de `close()`.

   `close()` continua só liberando recursos e pode ser chamado mais de uma vez. `finish()` é chamado pelo `MigrationRunner`, que é quem sabe o resultado, no momento em que a execução termina. Alternativa considerada: `close(success: bool)`. Rejeitada porque `close()` roda a partir de `__del__` e do caminho de erro da factory, pontos que não conhecem o resultado e cujo momento não é controlado.

   Um output que receber `close()` sem `finish(True)` deve tratar a execução como não confirmada. Para o `SqlFileOutput` isso não muda nada. Para um futuro `DirectDatabaseOutput` significa `ROLLBACK`.

2. Chamar `finish()` uma vez por execução.

   No caminho de sucesso o runner faz o último `flush()`, chama `finish(True)` e só depois grava `success` no manifesto. Se `finish(True)` levantar exceção, a execução é registrada como `failed` e o runner não chama `finish(False)` em seguida. Na falha, o runner chama `finish(False)` antes de gravar `failed`. Se esse `finish(False)` também levantar exceção, o erro vai para o log e o manifesto ainda é gravado.

   Alternativa considerada: chamar `finish(False)` depois de um `finish(True)` que falhou. Rejeitada porque obriga cada output a lidar com duas finalizações e não ajuda o caso real: se o `COMMIT` falhou, a transação já foi desfeita pelo banco.

3. `SqlFileOutput.finish()` só faz `flush()`, nos dois casos.

   Os arquivos SQL são artefatos de auditoria. Hoje uma execução que falha deixa os arquivos parciais no disco, e isso continua igual. O `flush()` só antecipa a gravação do buffer, que antes acontecia no `close()`.

4. `MirrorOutput` repassa as chamadas na ordem em que recebeu os outputs.

   `open_phase`, `write`, `comment` e `flush` param no primeiro erro, que derruba a execução. `finish` e `close` chegam a todos os outputs mesmo que um deles falhe, e o primeiro erro é relançado no fim. Assim, uma falha no banco não impede o arquivo de receber o `finish`, e um erro ao fechar o arquivo não deixa uma transação aberta.

   O `comment()` é repassado como texto e não como SQL já renderizado. Cada output decide como representar o comentário.

   `MirrorOutput()` sem nenhum output levanta `ValueError`. Com um só output ele funciona, mas não tem utilidade prática.

5. Criar `describe()` no contrato e gravar o resultado no manifesto.

   `describe()` devolve um dicionário serializável em JSON. O `SqlFileOutput` devolve `{'type': 'sql-file'}`. O `MirrorOutput` devolve `{'type': 'mirror', 'outputs': [...]}` com a descrição de cada filho. O manifesto grava esse dicionário na chave `output`.

   Alternativa considerada: gravar o nome da classe do output. Rejeitada porque o `DirectDatabaseOutput` vai precisar registrar o modo (`apply` ou `rollback`), e um dicionário cobre isso sem mudar o manifesto de novo.

6. Remover `self.file.seek(0, 2)` de `BaseMigration.dump_tables()`.

   O `pg_dump` grava em `02_dump_compatible_tables.sql`, mas `self.file` aponta para o arquivo da fase aberta, que nesse momento é `00_setup.sql`. A chamada não tem efeito no conteúdo. Com `MirrorOutput`, `self.file` é `None` e a chamada levantaria `AttributeError`.

## Risks / Trade-offs

- Outputs de fora do core quebram por não ter `finish()` ou `describe()` -> aceito, porque o projeto não está em produção e só os fakes de teste implementam o contrato fora do core.
- `MirrorOutput` com um output lento deixa a execução inteira lenta -> aceitável nesta etapa. Paralelismo fica para o item 12 do roadmap.
- O `MirrorOutput` repassa tudo de forma síncrona e não desfaz o que um filho já recebeu quando outro falha -> o `finish(False)` existe para isso, e cada output decide como desfazer.
- A propriedade `BaseMigration.file` devolve `None` com `MirrorOutput` -> já era o comportamento documentado para outputs que não gravam em arquivo.

## Migration Plan

1. Atualizar o contrato em `core/outputs/base.py`.
2. Implementar `finish()` e `describe()` no `SqlFileOutput`.
3. Criar `core/outputs/mirror.py` e exportar `MirrorOutput`.
4. Atualizar `MigrationRunner` para chamar `finish()`.
5. Passar a descrição do output para `ArtifactManager.write_manifest()` pelo contexto e pela factory.
6. Remover o `seek` de `BaseMigration.dump_tables()`.
7. Atualizar fakes de teste, testes e documentação.

Rollback: reverter a change. Não há dado persistente novo além da chave `output` no manifesto de execuções feitas com a change.

## Open Questions

- A escolha do output pela factory ou pelo CLI fica para a change do `DirectDatabaseOutput`, quando houver mais de um output concreto.
