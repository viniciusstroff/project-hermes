# AcmeEdu

Cliente fictício para rodar o Hermes de ponta a ponta sem dado real. A AcmeEdu é uma instituição de ensino com uma V1 legada e uma V2 nova, as duas em Docker. A migração exercita os recursos do core usados pelos clientes reais: `TableMigration`, `MultiTargetMigration`, strategies de limpeza, mapa de referência lido da V2, `pg_dump` de tabela compatível, `NativeQuery`, limpeza, suspensão de triggers e ajuste de sequences por operações.

Nada aqui representa uma instituição real. Nomes, CPFs, telefones e e-mails são gerados. Os e-mails usam o domínio reservado `.example`.

## Rodando

Na raiz do repositório:

```bash
# Hermes e as bases da AcmeEdu, todos na rede hermes-net
docker compose up -d
docker compose -f AcmeEdu/docker-compose.yml up -d
cp AcmeEdu/.env.example AcmeEdu/.env

# Teste rápido: 100 linhas por consulta, terminando em ROLLBACK
docker exec project-hermes python3 Migracao.py AcmeEdu --debug 100 --dry-run

# Carga completa: gera, aplica e confere
docker exec project-hermes python3 Migracao.py AcmeEdu --apply
docker exec project-hermes python3 AcmeEdu/conferir.py
```

Para aplicar uma execução já gerada com o `Aplicar.py`:

```bash
docker exec -e PGHOST=acmeedu-v2 -e PGDATABASE=acmeedu_v2 -e PGUSER=acmeedu -e PGPASSWORD=acmeedu \
    project-hermes python3 Aplicar.py runs/AcmeEdu/<timestamp> --dry-run
```

No host, as bases ficam em `localhost:5441` (V1) e `localhost:5442` (V2), com usuário e senha `acmeedu`. A conferência roda fora do Docker com as variáveis sobrescritas:

```bash
SOURCE_V1_HOST=localhost SOURCE_V1_PORT=5441 TARGET_HOST=localhost TARGET_PORT=5442 python3 AcmeEdu/conferir.py
```

Os scripts de `banco/` só rodam na primeira subida, com o volume vazio. Para recriar as bases:

```bash
docker compose -f AcmeEdu/docker-compose.yml down -v
docker compose -f AcmeEdu/docker-compose.yml up -d
```

A AcmeEdu declara o destino e uma origem só, `v1`. Ela não usa Firebird, então o Hermes não tenta essa conexão nem importa o `firebirdsql`.

## As bases

| Arquivo | Conteúdo |
|---|---|
| `banco/v1_esquema.sql` | V1 legada, PostgreSQL 9.6 |
| `banco/v1_dados.sql` | dados gerados por aritmética sobre o número da linha, sem `random()`, então a base sai sempre igual |
| `banco/v2_esquema.sql` | V2, PostgreSQL 16, com a tabela de domínio `student_statuses` já preenchida e a extensão `amcheck` |

A V1 roda no 9.6, a versão da V1 real. A cópia de `holidays` é linha a linha por padrão. Com `SOURCE_V1_NATIVE_COPY=pg_dump` no `.env`, ela vai pelo `pg_dump` 9.6 da imagem do Hermes, que não exporta de servidor mais novo e por isso é conferido ao conectar.

A V1 tem sujeira proposital:

- nomes em maiúsculas, com espaço sobrando ou com apóstrofo (`D'ÁVILA`);
- CPF com máscara, sem máscara, vazio ou nulo;
- telefone só com dígitos, com máscara ou com `+55`;
- datas em texto, vazias ou impossíveis (`31/02/2001`, `20230230`);
- e-mail no meio de outro texto (`Email: Aluno5@AcmeEdu.Example / tel. recado`);
- códigos de uma letra, alguns em minúscula;
- observações com `;`, aspas e barra invertida, algumas terminando em barra.

Volume: 6 cursos, 25 professores, 48 disciplinas, 120 turmas, 1500 alunos, 6000 matrículas e 12 feriados.

## Da V1 para a V2

A V2 usa nomes em inglês, sem prefixo, com chave `id`. O script ajusta as sequences com `reset_identity(..., column='id')`. O adapter acha cada sequence pelo `pg_get_serial_sequence`.

| V2 | V1 | Mudança |
|---|---|---|
| `courses` | `tb_curso` | renomeação; modalidade `P`/`E` vira 1/2 e `ativo` `S`/`N` vira boolean |
| `people` | dados pessoais de `tb_aluno` e `tb_professor` | agrupamento: uma linha por pessoa |
| `students` | dados acadêmicos de `tb_aluno` | separação; situação vira `status_id` pelo mapa lido de `student_statuses` |
| `teachers` | `tb_professor` | separação; titulação em texto livre vira 1, 2 ou 3 |
| `phones` | `fone` do aluno, `fone1` e `fone2` do professor | separação e agrupamento, sem telefone repetido para a mesma pessoa |
| `guardians` | colunas de responsável em `tb_aluno` | separação, só quando há responsável |
| `subjects` | `tb_disciplina` | renomeação |
| `classes` | `tb_turma` | `2024/1` vira `year` e `term` |
| `enrollments` | `tb_matricula` | data `AAAAMMDD` vira `date`, `85%` vira `85.00`, status vira número |
| `holidays` | `holidays` | nenhuma; está em `compatible_entities()` e vai para o `02_copy_compatible_entities.sql` |

O id de cada pessoa sai da V1, porque a geração escreve SQL sem ler o destino:

- aluno: `people.id` = `cod_aluno`;
- professor que não é aluno: `people.id` = `100000 + cod_prof`;
- professor que também é aluno, com o mesmo CPF: reaproveita a pessoa do aluno.

Quatro professores da V1 têm o CPF e o celular de um aluno, em outro formato, para exercitar esse caso.

As regras próprias do cliente (nome, CPF, telefone, e-mail, titulação, semestre, frequência) são funções puras no início do `AcmeEdu.py`, testadas em `tests/test_acmeedu.py`.

## Conferência

O `conferir.py` lê as duas bases, calcula o esperado a partir da V1 com as mesmas funções de limpeza e compara com a V2:

- contagem de linhas por tabela;
- nome, CPF, e-mail e telefones de cada pessoa;
- professores que também são alunos apontando para a pessoa do aluno;
- observações, inclusive as com barra invertida, iguais às da V1;
- CPF sem repetição e nomes sem espaço sobrando;
- chaves estrangeiras sem órfãos, já que a carga roda com as triggers desligadas;
- sequences maiores ou iguais ao maior `id`;
- `bt_index_check` com `heapallindexed` do `amcheck` em todos os índices.

Mostra o esperado e o encontrado de cada verificação e sai com código 1 se alguma falhar. Numa carga completa, todas as 58 verificações passam.
