## Context

O `Acme` mostra a forma de um script de cliente, mas não tem banco. Para rodar o Hermes de ponta a ponta hoje é preciso a V1 de um cliente real, por túnel SSH. O `DefaultMigrationContextFactory` também conecta sempre no Firebird, então um cliente que só tem PostgreSQL não consegue nem montar o contexto.

O `pg_dump` da imagem Docker do Hermes é o 9.6, porque a V1 real roda nessa versão. Um `pg_dump` 9.6 não exporta de um servidor mais novo.

O `dump_tables()` exporta todas as tabelas da V1 menos as listadas em `dump_ignoring_tables()` e `dump_non_existing_tables()`. Então toda tabela que passa por transformação precisa estar nessas listas, junto com a sequence dela.

## Goals / Non-Goals

**Goals:**

- Ter V1 e V2 fictícias que sobem com um comando e sempre geram os mesmos dados.
- Exercitar no mesmo cliente os recursos do core usados pelos clientes reais: `TableMigration`, `MultiTargetMigration`, strategies de limpeza, mapa de referência lido da V2, `pg_dump` de tabela compatível, triggers, índices e sequences.
- Ter uma conferência que diga se a carga está certa sem olhar linha a linha.
- Fechar as tasks 6.4 e 3.2 das changes anteriores com uma execução gerada de verdade.

**Non-Goals:**

- Não reproduzir o esquema da V1 real nem o volume de um cliente real.
- Não usar Firebird. O caminho do Firebird continua coberto pelos testes com dublês.
- Não corrigir o helper de índices nesta change (ver Open Questions).
- Não medir desempenho.

## Decisions

1. Nome e lugar: `AcmeEdu/`, versionado.

   Segue a convenção de nomes fictícios do projeto (`Acme`, `AcmeAlpha`). Os scripts dos bancos ficam em `AcmeEdu/banco/`, porque o `.gitignore` ignora `*/sql/*.sql`. Ao contrário dos clientes reais, que ficam fora do Git, este é versionado: não tem dado real e serve de exemplo e de teste.

2. Duas bases próprias no `AcmeEdu/docker-compose.yml`.

   | Serviço | Imagem | Base | Papel |
   |---|---|---|---|
   | `acmeedu-v1` | `postgres:9.6-alpine` | `acmeedu_v1` | origem legada, criada por `v1_esquema.sql` e `v1_dados.sql` |
   | `acmeedu-v2` | `postgres:16-alpine` | `acmeedu_v2` | destino, criado por `v2_esquema.sql` |

   As duas entram na rede `hermes-net`, então o container `project-hermes` acessa `acmeedu-v1:5432` e `acmeedu-v2:5432`. O `target-db` genérico continua livre para outros usos. A V1 roda no 9.6 para o `pg_dump` da imagem funcionar e para ficar parecida com a V1 real.

3. Dados gerados em SQL, com semente fixa.

   O `v1_dados.sql` usa `setseed()` e `generate_series()`, com listas fixas de nomes, cursos e disciplinas. Não precisa de Python nem de biblioteca de dados falsos, roda no init do container e gera sempre o mesmo conteúdo. Volume previsto: 6 cursos, 25 professores, 48 disciplinas, 120 turmas, 1500 alunos e cerca de 6000 matrículas.

   A sujeira é proposital e sai de regras determinísticas, por exemplo "um em cada 7 alunos tem CPF sem máscara":

   - nomes em maiúsculas, com espaço duplo e com apóstrofo (`D'ÁVILA`);
   - CPF com máscara, sem máscara ou vazio;
   - telefone como `(11) 98765-4321`, `11987654321` ou `+55 11 98765-4321`;
   - datas em texto `DD/MM/AAAA`, algumas vazias ou impossíveis (`31/02/2001`);
   - data de matrícula em texto `AAAAMMDD`;
   - e-mail no meio de outro texto (`Email: Fulano@Exemplo.com / tel 1234`);
   - observações com `;`, aspas e barra invertida (`C:\docs\historico`);
   - códigos de uma letra para situação, modalidade e status.

4. Estrutura da V1 (legada).

   | Tabela | Colunas principais |
   |---|---|
   | `tb_curso` | `cod_curso`, `nome_curso`, `sigla`, `modalidade` (`P`/`E`), `carga_horaria`, `ativo` (`S`/`N`) |
   | `tb_professor` | `cod_prof`, `nome_prof`, `cpf`, `email`, `titulacao` (texto livre), `fone1`, `fone2` |
   | `tb_disciplina` | `cod_disc`, `nome_disc`, `cod_curso`, `cod_prof`, `carga_horaria` |
   | `tb_turma` | `cod_turma`, `cod_disc`, `ano_semestre` (`2024/1`), `sala` |
   | `tb_aluno` | `cod_aluno`, `nome_aluno`, `dt_nasc` (texto), `cpf`, `fone`, `email_contato`, `sexo`, `situacao` (`A`/`T`/`F`/`C`), `cod_curso`, `nome_resp`, `fone_resp`, `parentesco`, `obs`, `dt_cadastro` |
   | `tb_matricula` | `cod_matricula`, `cod_aluno`, `cod_turma`, `dt_matricula` (`AAAAMMDD`), `nota_final`, `frequencia` (`85%`), `status` (`M`/`A`/`R`/`T`) |
   | `holidays` | `id`, `holiday_date`, `description`: tabela recente, já no padrão novo |
   | `tb_log_acesso` | log de auditoria, não migrado |

5. Estrutura da V2 e de onde vem cada tabela.

   Nomes em inglês, em `snake_case` e sem prefixo. A chave é `id`, com a sequence `<tabela>_id_seq`. O core assume `f_id` por padrão no `reset_sequence_sql`, então o cliente passa `column='id'` ao `write_reset_sequences`. Fica de exemplo de um destino que não segue o padrão `f_` dos clientes atuais.

   | Tabela V2 | Origem | Mudança | Recurso do core |
   |---|---|---|---|
   | `courses` | `tb_curso` | renomeação | `Rename`, `Lookup` (modalidade → 1/2), `Transform` (`S`/`N` → boolean) |
   | `people` | dados pessoais de `tb_aluno` e de `tb_professor` | agrupamento | `MultiTargetMigration`, `CpfClean`, `DateConvert`, `EmailExtract`, `Transform` (nome) |
   | `students` | dados acadêmicos de `tb_aluno` | separação | `MultiTargetMigration`, `Lookup` com o mapa de `student_statuses` |
   | `teachers` | `tb_professor` | separação | `Lookup` (titulação → 1/2/3) |
   | `phones` | `fone` de `tb_aluno`, `fone1` e `fone2` de `tb_professor` | separação e agrupamento | `MultiTargetMigration` com uma condição por telefone, `PhoneClean` |
   | `guardians` | colunas de responsável em `tb_aluno` | separação | `MultiTargetMigration`, só quando `nome_resp` não é vazio |
   | `subjects` | `tb_disciplina` | renomeação | `Rename` |
   | `classes` | `tb_turma` | renomeação | `Transform` (`2024/1` → `year` e `term`) |
   | `enrollments` | `tb_matricula` | renomeação | `DateConvert` (`%Y%m%d`), `Transform` (`85%` → `85.00`), `Lookup` (status) |
   | `student_statuses` | criada já preenchida no `v2_esquema.sql` | tabela de domínio nova | mapa de referência lido da V2 (`load_reference_map`) |
   | `holidays` | `holidays` | nenhuma | `pg_dump` para o `02_dump_compatible_tables.sql` |

   O agrupamento em `people` junta alunos e professores numa tabela só. Um professor que também é aluno, com o mesmo CPF, vira uma pessoa só. As tabelas `students` e `teachers` apontam para ela pelo `person_id`. O `phones` guarda os telefones de qualquer pessoa, com `person_id` e a posição do telefone.

   As tabelas `tb_*`, as sequences delas e `tb_log_acesso` entram em `dump_ignoring_tables()`. Só `holidays` e a sequence dela vão pelo `pg_dump`.

   Os nomes saem com a primeira letra de cada palavra em maiúscula e sem espaço sobrando. As partículas `da`, `de`, `do`, `dos`, `das` e `e` ficam em minúscula. Essa regra e as outras transformações próprias do cliente ficam em funções puras no `AcmeEdu.py`, testadas sem banco.

6. Ids das pessoas derivados da V1.

   A geração escreve SQL sem executar nada no destino, então não consegue ler o `id` que a sequence de `people` daria a cada linha. O id da pessoa sai das chaves da V1:

   - aluno: `people.id` = `cod_aluno`;
   - professor que não é aluno: `people.id` = `100000 + cod_prof`;
   - professor que também é aluno: usa o `cod_aluno` do aluno com o mesmo CPF (só dígitos). Sem CPF, não há junção.

   A consulta dos professores na V1 resolve isso com um `LEFT JOIN LATERAL` em `tb_aluno` pelo CPF limpo e devolve o `person_id` pronto. Os professores que já são alunos não geram linha em `people`. No fim, o `write_reset_sequences` ajusta `people_id_seq` para o maior id.

   Alternativa considerada: cada INSERT de `students` e `teachers` buscar o id com `(SELECT id FROM people WHERE cpf = ...)`. Rejeitada porque não funciona para quem não tem CPF e faz uma consulta por linha na aplicação.

   O gerador da V1 garante CPF único entre os alunos e faz alguns professores terem o CPF de um aluno, com outra máscara. Assim o agrupamento tem casos reais para resolver.

7. Firebird opcional no core.

   Se `FB_V1_HOST` não estiver no `.env` ou estiver vazio, o `DefaultMigrationContextFactory` não conecta no Firebird, não registra a origem `fb_v1` e deixa `fb_v1_conn` como `None`. Um cliente que chama `context.engine('fb_v1')` sem Firebird configurado recebe um erro dizendo que a origem não está configurada. Com as chaves preenchidas, nada muda.

   Alternativa considerada: subir um Firebird no compose do cliente fictício. Rejeitada porque o cliente não usa Firebird e a imagem só serviria para satisfazer uma conexão obrigatória.

8. Conferência em Python, lendo as duas bases.

   O `AcmeEdu/conferir.py` usa as conexões do `.env` do cliente, calcula o esperado consultando a V1 e compara com a V2. Mostra uma linha por verificação, com o esperado e o encontrado. Sai com código diferente de 0 se alguma falhar. Calcular o esperado na V1 evita números fixos que ficariam desatualizados quando os dados gerados mudarem. As verificações são:

   - contagem por tabela contra a V1, considerando as linhas que viram tabelas novas (responsáveis e telefones) e as pessoas agrupadas;
   - professores que também são alunos apontando para a mesma pessoa do aluno;
   - nenhum CPF ou telefone com caractere que não seja dígito;
   - nenhum nome com espaço duplo ou nas pontas;
   - observações com barra invertida iguais às da V1;
   - FKs sem órfãos;
   - sequences maiores ou iguais ao maior `id`;
   - `bt_index_check(..., heapallindexed => true)` do `amcheck` em todos os índices da V2.

## Risks / Trade-offs

- O cliente fictício pode divergir dos clientes reais com o tempo → ele cobre os recursos do core, não as regras de um cliente. Novos recursos do core devem entrar nele.
- A imagem `postgres:9.6-alpine` está fora de suporte → é usada só para a origem fictícia, que precisa ser compatível com o `pg_dump` 9.6 da imagem do Hermes.
- Os dados gerados mudam se alguém editar o `v1_dados.sql` → a conferência calcula o esperado consultando a V1, sem números fixos.

## Migration Plan

1. Firebird opcional no core, com testes.
2. Esquemas e dados da V1 e esquema da V2, com o compose.
3. Script `AcmeEdu.py` e testes das transformações.
4. Rodar a migração completa em Docker: geração, dry run, apply e conferência.
5. Usar essa execução para fechar a task 6.4 de `aplicar-artefatos-sql-no-destino` e, gerando antes e depois da correção da barra invertida, a task 3.2 de `preservar-barra-invertida-nos-literais`.
6. Documentar.

Rollback: remover `AcmeEdu/` e reverter a mudança no `core/context.py`.

## Open Questions

- O helper de índices (`set_index_commands_for`) desativa índices com `UPDATE pg_index SET indisready = false` e reativa com `indisready = true`, sem `REINDEX`. Respondida em 2026-11-25 com testes na V2 da AcmeEdu:
  - Com nome de índice, como fazem o checklist de novo cliente, o `Acme` e o `AcmeEdu`, o `UPDATE` compara o nome com `indrelid`, que é o OID da tabela. Ele atinge 0 linhas e o helper não faz nada. A carga completa da AcmeEdu passou no `amcheck` em todos os índices.
  - Com nome de tabela, os índices da tabela são desligados de fato. Numa transação que desligou o índice de `holidays`, inseriu uma linha e religou o índice, a busca pelo índice não achou a linha (a busca sem índice achou), a chave primária aceitou um segundo `id = 100` e o `bt_index_check` acusou `heap tuple ... lacks matching index tuple`. O teste terminou em `ROLLBACK`.
  - Resta saber se algum cliente real passa nome de tabela. Se passar, os índices dessas tabelas no destino estão incompletos desde a carga e precisam de `REINDEX`. A correção do helper fica para outra change.
