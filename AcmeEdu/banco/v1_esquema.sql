-- V1 legada da AcmeEdu, uma instituição de ensino fictícia.
-- Compatível com PostgreSQL 9.6, a versão da V1 e do pg_dump da imagem do Hermes.
-- Os nomes, os tipos e os códigos de uma letra imitam um sistema antigo de propósito.

CREATE TABLE tb_curso (
    cod_curso     serial PRIMARY KEY,
    nome_curso    varchar(120) NOT NULL,
    sigla         varchar(10),
    modalidade    char(1),                 -- P presencial, E a distância
    carga_horaria integer,
    ativo         char(1) DEFAULT 'S'      -- S ou N
);

CREATE TABLE tb_professor (
    cod_prof   serial PRIMARY KEY,
    nome_prof  varchar(150) NOT NULL,
    cpf        varchar(20),
    email      varchar(200),
    titulacao  varchar(30),                -- texto livre
    fone1      varchar(30),
    fone2      varchar(30)
);

CREATE TABLE tb_disciplina (
    cod_disc      serial PRIMARY KEY,
    nome_disc     varchar(150) NOT NULL,
    cod_curso     integer REFERENCES tb_curso,
    cod_prof      integer REFERENCES tb_professor,
    carga_horaria integer
);

CREATE TABLE tb_turma (
    cod_turma    serial PRIMARY KEY,
    cod_disc     integer REFERENCES tb_disciplina,
    ano_semestre varchar(6),               -- 2024/1
    sala         varchar(20)
);

CREATE TABLE tb_aluno (
    cod_aluno     serial PRIMARY KEY,
    nome_aluno    varchar(150) NOT NULL,
    dt_nasc       varchar(10),             -- DD/MM/AAAA em texto
    cpf           varchar(20),
    fone          varchar(30),
    email_contato varchar(200),
    sexo          char(1),
    situacao      char(1),                 -- A ativo, T trancado, F formado, C cancelado
    cod_curso     integer REFERENCES tb_curso,
    nome_resp     varchar(150),
    fone_resp     varchar(30),
    parentesco    varchar(20),
    obs           text,
    dt_cadastro   timestamp
);

CREATE TABLE tb_matricula (
    cod_matricula serial PRIMARY KEY,
    cod_aluno     integer REFERENCES tb_aluno,
    cod_turma     integer REFERENCES tb_turma,
    dt_matricula  varchar(8),              -- AAAAMMDD em texto
    nota_final    numeric(4,2),
    frequencia    varchar(5),              -- 85%
    status        char(1)                  -- M matriculado, A aprovado, R reprovado, T trancado
);

-- Tabela criada depois, já no padrão da V2. Vai para o destino pelo pg_dump.
CREATE TABLE holidays (
    id           serial PRIMARY KEY,
    holiday_date date NOT NULL,
    description  varchar(100) NOT NULL
);

-- Log de auditoria que não é migrado.
CREATE TABLE tb_log_acesso (
    cod_log serial PRIMARY KEY,
    usuario varchar(50),
    dt_hora timestamp,
    acao    varchar(200)
);
