-- Dados fictícios da V1 da AcmeEdu.
--
-- Tudo sai de aritmética sobre o número da linha, sem random(). Recriar a base
-- gera sempre o mesmo conteúdo. CPFs, telefones e e-mails são inventados: os
-- e-mails usam o domínio reservado .example.
--
-- A sujeira é proposital e cobre os casos que o core limpa:
--   nomes em maiúsculas, com espaço sobrando ou com apóstrofo;
--   CPF com máscara, sem máscara, vazio ou nulo;
--   telefone só com dígitos, com máscara ou com +55;
--   datas em texto, vazias ou impossíveis (31/02/2001, 20230230);
--   e-mail no meio de outro texto e com maiúsculas;
--   códigos de uma letra, inclusive em minúscula;
--   observações com ;, aspas e barra invertida, uma delas terminando em barra.
-- Quatro professores têm o CPF de um aluno, com outra máscara, para o agrupamento em people.

SET standard_conforming_strings = on;

CREATE FUNCTION pg_temp.primeiro_nome(n integer) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT (ARRAY['Ana', 'Bruno', 'Carla', 'Daniel', 'Eduarda', 'Felipe', 'Gabriela', 'Henrique',
                  'Isabela', 'João', 'Karina', 'Lucas', 'Mariana', 'Nícolas', 'Olívia', 'Pedro',
                  'Quitéria', 'Rafael', 'Sofia', 'Tiago', 'Úrsula', 'Valter', 'Wesley', 'Yasmin'])[1 + n % 24]
$$;

CREATE FUNCTION pg_temp.sobrenome(n integer) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT (ARRAY['Silva', 'Souza', 'Oliveira', 'Santos', 'Pereira', 'Costa', 'Rodrigues', 'Almeida',
                  'Nascimento', 'Lima', 'Araújo', 'Fernandes', 'Carvalho', 'Gomes', 'Martins', 'Rocha',
                  'Ribeiro', 'D''Ávila', 'Barros', 'Freitas'])[1 + n % 20]
$$;

CREATE FUNCTION pg_temp.nome_completo(n integer) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT pg_temp.primeiro_nome(n)
        || CASE n % 4 WHEN 0 THEN ' da ' WHEN 1 THEN ' de ' ELSE ' ' END
        || pg_temp.sobrenome(n * 7)
        || ' ' || pg_temp.sobrenome(n * 11 + 3)
$$;

CREATE FUNCTION pg_temp.sujar_nome(nome text, n integer) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN n % 11 = 0 THEN '  ' || upper(nome) || ' '
        WHEN n % 5 = 0 THEN regexp_replace(nome, ' ', '  ')
        WHEN n % 3 = 0 THEN upper(nome)
        ELSE nome
    END
$$;

-- Onze dígitos inventados a partir de uma base.
CREATE FUNCTION pg_temp.cpf(base bigint) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT lpad(base::text, 9, '0') || '00'
$$;

CREATE FUNCTION pg_temp.mascarar_cpf(cpf text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT substr(cpf, 1, 3) || '.' || substr(cpf, 4, 3) || '.' || substr(cpf, 7, 3) || '-' || substr(cpf, 10, 2)
$$;

-- Celular com DDD, onze dígitos. Bases diferentes geram números diferentes.
CREATE FUNCTION pg_temp.fone_digitos(base integer) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT (ARRAY['11', '21', '31', '41', '51', '61', '71', '81'])[1 + base % 8]
        || '9' || lpad(((base::bigint * 7919) % 100000000)::text, 8, '0')
$$;

CREATE FUNCTION pg_temp.formatar_fone(digitos text, n integer) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE n % 3
        WHEN 0 THEN digitos
        WHEN 1 THEN '(' || substr(digitos, 1, 2) || ') ' || substr(digitos, 3, 5) || '-' || substr(digitos, 8, 4)
        ELSE '+55 ' || substr(digitos, 1, 2) || ' ' || substr(digitos, 3, 5) || '-' || substr(digitos, 8, 4)
    END
$$;

-- Cursos ---------------------------------------------------------------------

INSERT INTO tb_curso (cod_curso, nome_curso, sigla, modalidade, carga_horaria, ativo) VALUES
    (1, 'ADMINISTRAÇÃO', 'ADM', 'P', 3000, 'S'),
    (2, 'Análise e Desenvolvimento de Sistemas', 'ADS', 'E', 2400, 'S'),
    (3, 'pedagogia ', 'PED', 'p', 3200, 'S'),
    (4, 'Ciências Contábeis', 'CCO', 'E', 3000, 'N'),
    (5, 'ENFERMAGEM', 'ENF', 'P', 4000, 's'),
    (6, 'Direito', 'DIR', 'P', 3700, 'S');

-- Professores ----------------------------------------------------------------

INSERT INTO tb_professor (cod_prof, nome_prof, cpf, email, titulacao, fone1, fone2)
SELECT j,
       CASE WHEN j % 6 = 0 THEN pg_temp.sujar_nome(pg_temp.nome_completo(j * 50), j + 1)
            ELSE pg_temp.sujar_nome(pg_temp.nome_completo(j * 37 + 5), j) END,
       -- j % 6 = 0: mesmo CPF do aluno j * 50, sem máscara (o aluno tem máscara)
       CASE WHEN j % 6 = 0 THEN pg_temp.cpf(100000000 + j * 50 * 7)
            WHEN j % 2 = 0 THEN pg_temp.mascarar_cpf(pg_temp.cpf(500000000 + j * 11))
            ELSE pg_temp.cpf(500000000 + j * 11) END,
       CASE WHEN j % 4 = 0 THEN 'Prof' || j || '@AcmeEdu.Example'
            ELSE 'prof' || j || '@acmeedu.example' END,
       (ARRAY['Especialista', 'MESTRE', 'Doutor', 'dra.', 'mestrado', 'Especialização', '', NULL])[1 + j % 8],
       -- j % 6 = 0: o mesmo celular do aluno, em outro formato
       CASE WHEN j % 6 = 0 THEN pg_temp.formatar_fone(pg_temp.fone_digitos(j * 50), j * 50 + 1)
            ELSE pg_temp.formatar_fone(pg_temp.fone_digitos(50000 + j), j) END,
       CASE WHEN j % 5 = 0 THEN ''
            WHEN j % 2 = 0 THEN pg_temp.formatar_fone(pg_temp.fone_digitos(60000 + j), j + 2)
            ELSE NULL END
FROM generate_series(1, 25) AS j;

-- Disciplinas e turmas -------------------------------------------------------

INSERT INTO tb_disciplina (cod_disc, nome_disc, cod_curso, cod_prof, carga_horaria)
SELECT k,
       (ARRAY['Matemática', 'Português', 'Estatística', 'Ética', 'Metodologia Científica', 'Gestão de Projetos',
              'Programação', 'Banco de Dados', 'Anatomia', 'Direito Civil', 'Contabilidade Geral', 'Didática'])[1 + (k - 1) % 12]
           || ' ' || (ARRAY['I', 'II', 'III', 'IV'])[1 + (k - 1) / 12],
       1 + (k - 1) % 6,
       1 + (k * 7) % 25,
       40 + (k % 3) * 20
FROM generate_series(1, 48) AS k;

INSERT INTO tb_turma (cod_turma, cod_disc, ano_semestre, sala)
SELECT t,
       1 + (t - 1) % 48,
       (ARRAY['2022/1', '2022/2', '2023/1', '2023/2', '2024/1'])[1 + (t - 1) / 24],
       CASE WHEN t % 4 = 0 THEN 'LAB-' || (1 + t % 5) ELSE 'Sala ' || (100 + t % 30) END
FROM generate_series(1, 120) AS t;

-- Alunos ---------------------------------------------------------------------

INSERT INTO tb_aluno (cod_aluno, nome_aluno, dt_nasc, cpf, fone, email_contato, sexo, situacao, cod_curso,
                      nome_resp, fone_resp, parentesco, obs, dt_cadastro)
SELECT i,
       pg_temp.sujar_nome(pg_temp.nome_completo(i), i),
       CASE WHEN i % 31 = 0 THEN NULL
            WHEN i % 29 = 0 THEN '31/02/2001'
            WHEN i % 17 = 0 THEN ''
            ELSE to_char(date '1985-01-01' + (i * 37) % 9000, 'DD/MM/YYYY') END,
       CASE WHEN i % 19 = 0 THEN NULL
            WHEN i % 13 = 0 THEN ''
            WHEN i % 7 = 0 THEN pg_temp.cpf(100000000 + i * 7)
            ELSE pg_temp.mascarar_cpf(pg_temp.cpf(100000000 + i * 7)) END,
       CASE WHEN i % 8 = 3 THEN ''
            WHEN i % 4 = 3 THEN NULL
            ELSE pg_temp.formatar_fone(pg_temp.fone_digitos(i), i) END,
       CASE WHEN i % 9 = 0 THEN ''
            WHEN i % 5 = 0 THEN 'Email: Aluno' || i || '@AcmeEdu.Example / tel. recado'
            ELSE 'aluno' || i || '@acmeedu.example' END,
       CASE WHEN i % 10 = 0 THEN '' WHEN i % 2 = 0 THEN 'F' ELSE 'M' END,
       CASE WHEN i % 20 = 0 THEN 'C'
            WHEN i % 10 = 5 THEN 'T'
            WHEN i % 7 = 3 THEN 'F'
            WHEN i % 23 = 0 THEN 'a'
            ELSE 'A' END,
       1 + i % 6,
       CASE WHEN i % 33 = 0 THEN ''
            WHEN i % 3 = 0 THEN pg_temp.sujar_nome(pg_temp.nome_completo(i + 5000), i + 1)
            ELSE NULL END,
       CASE WHEN i % 3 = 0 AND i % 33 <> 0 THEN pg_temp.formatar_fone(pg_temp.fone_digitos(70000 + i), i + 1) END,
       CASE WHEN i % 3 = 0 AND i % 33 <> 0 THEN (ARRAY['Mãe', 'Pai', 'Avó', 'Tio', 'mae'])[1 + i % 5] END,
       CASE WHEN i % 50 = 0 THEN 'Documentos em \\servidor\secretaria\'
            WHEN i % 8 = 0 THEN 'Histórico em C:\docs\historico\' || i || '.pdf'
            WHEN i % 12 = 0 THEN 'Bolsista; renovar em 2025'
            WHEN i % 14 = 0 THEN 'Responsável avisou: "chega às 7h"'
            ELSE NULL END,
       timestamp '2015-01-05 08:00' + i * interval '2 days' + (i % 10) * interval '1 hour'
FROM generate_series(1, 1500) AS i;

-- Matrículas: quatro turmas diferentes por aluno ------------------------------

INSERT INTO tb_matricula (cod_matricula, cod_aluno, cod_turma, dt_matricula, nota_final, frequencia, status)
SELECT m.cod_matricula,
       m.cod_aluno,
       t.cod_turma,
       CASE WHEN m.cod_matricula % 97 = 0 THEN '20230230'
            WHEN m.cod_matricula % 40 = 0 THEN ''
            ELSE substr(t.ano_semestre, 1, 4)
                 || CASE WHEN substr(t.ano_semestre, 6, 1) = '1' THEN '02' ELSE '08' END
                 || lpad((1 + m.cod_matricula % 28)::text, 2, '0') END,
       CASE WHEN m.cod_matricula % 25 = 0 THEN NULL
            ELSE ((m.cod_matricula * 37) % 1001) / 100.0 END,
       CASE WHEN m.cod_matricula % 30 = 0 THEN ''
            WHEN m.cod_matricula % 11 = 0 THEN '100%'
            ELSE (50 + (m.cod_matricula * 11) % 50) || '%' END,
       CASE WHEN t.ano_semestre = '2024/1' THEN 'M'
            WHEN m.cod_matricula % 45 = 0 THEN 'T'
            WHEN (m.cod_matricula * 37) % 1001 >= 600 THEN 'A'
            ELSE 'R' END
FROM (
    SELECT (i - 1) * 4 + n AS cod_matricula, i AS cod_aluno, 1 + (i * 7 + n * 13) % 120 AS cod_turma
    FROM generate_series(1, 1500) AS i
    CROSS JOIN generate_series(1, 4) AS n
) AS m
JOIN tb_turma AS t ON t.cod_turma = m.cod_turma;

-- Feriados (tabela já no padrão novo) ----------------------------------------

INSERT INTO holidays (id, holiday_date, description) VALUES
    (1, '2024-01-01', 'Confraternização Universal'),
    (2, '2024-02-12', 'Carnaval'),
    (3, '2024-02-13', 'Carnaval'),
    (4, '2024-03-29', 'Sexta-feira Santa'),
    (5, '2024-04-21', 'Tiradentes'),
    (6, '2024-05-01', 'Dia do Trabalho'),
    (7, '2024-05-30', 'Corpus Christi'),
    (8, '2024-09-07', 'Independência do Brasil'),
    (9, '2024-10-12', 'Nossa Senhora Aparecida'),
    (10, '2024-11-02', 'Finados'),
    (11, '2024-11-15', 'Proclamação da República'),
    (12, '2024-12-25', 'Natal');

-- Log de acesso, que não é migrado ---------------------------------------------

INSERT INTO tb_log_acesso (cod_log, usuario, dt_hora, acao)
SELECT l,
       'usuario' || (1 + l % 12),
       timestamp '2024-01-02 07:00' + l * interval '37 minutes',
       (ARRAY['login', 'consulta aluno', 'emissão de boletim', 'logout'])[1 + l % 4]
FROM generate_series(1, 300) AS l;

-- Sequences da V1 alinhadas com os códigos gerados -----------------------------

SELECT setval('tb_curso_cod_curso_seq', (SELECT max(cod_curso) FROM tb_curso));
SELECT setval('tb_professor_cod_prof_seq', (SELECT max(cod_prof) FROM tb_professor));
SELECT setval('tb_disciplina_cod_disc_seq', (SELECT max(cod_disc) FROM tb_disciplina));
SELECT setval('tb_turma_cod_turma_seq', (SELECT max(cod_turma) FROM tb_turma));
SELECT setval('tb_aluno_cod_aluno_seq', (SELECT max(cod_aluno) FROM tb_aluno));
SELECT setval('tb_matricula_cod_matricula_seq', (SELECT max(cod_matricula) FROM tb_matricula));
SELECT setval('holidays_id_seq', (SELECT max(id) FROM holidays));
SELECT setval('tb_log_acesso_cod_log_seq', (SELECT max(cod_log) FROM tb_log_acesso));
