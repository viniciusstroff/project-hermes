-- V2 da AcmeEdu: estrutura nova, com nomes em inglês e sem prefixo.
-- A chave de cada tabela é id, com a sequence <tabela>_id_seq.
--
-- Mudanças em relação à V1:
--   people agrupa os dados pessoais de alunos e professores; quem é as duas
--   coisas (mesmo CPF) vira uma pessoa só;
--   students e teachers guardam só o papel de cada pessoa;
--   phones e guardians saem de colunas da V1 para tabelas próprias;
--   student_statuses é uma tabela de domínio nova, preenchida aqui.

CREATE TABLE student_statuses (
    id          smallint PRIMARY KEY,
    code        varchar(20) NOT NULL UNIQUE,
    description varchar(60) NOT NULL
);

INSERT INTO student_statuses (id, code, description) VALUES
    (1, 'active', 'Ativo'),
    (2, 'on_hold', 'Trancado'),
    (3, 'graduated', 'Formado'),
    (4, 'cancelled', 'Cancelado');

CREATE TABLE courses (
    id             serial PRIMARY KEY,
    name           varchar(120) NOT NULL,
    code           varchar(10) NOT NULL UNIQUE,
    modality       smallint NOT NULL CHECK (modality IN (1, 2)),  -- 1 presencial, 2 a distância
    workload_hours integer,
    active         boolean NOT NULL DEFAULT true
);

CREATE TABLE people (
    id         serial PRIMARY KEY,
    name       varchar(150) NOT NULL,
    cpf        char(11) CHECK (cpf ~ '^[0-9]{11}$'),
    birth_date date,
    email      varchar(200),
    gender     char(1) CHECK (gender IN ('F', 'M')),
    created_at timestamp NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX people_cpf_key ON people (cpf) WHERE cpf IS NOT NULL;
CREATE INDEX people_name_idx ON people (name);

CREATE TABLE students (
    id        serial PRIMARY KEY,
    person_id integer NOT NULL UNIQUE REFERENCES people,
    course_id integer NOT NULL REFERENCES courses,
    status_id smallint NOT NULL REFERENCES student_statuses,
    notes     text
);

CREATE TABLE teachers (
    id        serial PRIMARY KEY,
    person_id integer NOT NULL UNIQUE REFERENCES people,
    degree    smallint CHECK (degree IN (1, 2, 3))  -- 1 especialista, 2 mestre, 3 doutor
);

CREATE TABLE phones (
    id        serial PRIMARY KEY,
    person_id integer NOT NULL REFERENCES people,
    number    varchar(11) NOT NULL CHECK (number ~ '^[0-9]{10,11}$'),
    source    varchar(10) NOT NULL CHECK (source IN ('student', 'teacher')),
    UNIQUE (person_id, number)
);

CREATE TABLE guardians (
    id           serial PRIMARY KEY,
    student_id   integer NOT NULL REFERENCES students,
    name         varchar(150) NOT NULL,
    phone        varchar(11) CHECK (phone ~ '^[0-9]{10,11}$'),
    relationship varchar(20)
);

CREATE TABLE subjects (
    id             serial PRIMARY KEY,
    name           varchar(150) NOT NULL,
    course_id      integer NOT NULL REFERENCES courses,
    teacher_id     integer REFERENCES teachers,
    workload_hours integer
);

CREATE TABLE classes (
    id         serial PRIMARY KEY,
    subject_id integer NOT NULL REFERENCES subjects,
    year       smallint NOT NULL,
    term       smallint NOT NULL CHECK (term IN (1, 2)),
    room       varchar(20)
);

CREATE TABLE enrollments (
    id             serial PRIMARY KEY,
    student_id     integer NOT NULL REFERENCES students,
    class_id       integer NOT NULL REFERENCES classes,
    enrolled_at    date,
    final_grade    numeric(4,2) CHECK (final_grade BETWEEN 0 AND 10),
    attendance_pct numeric(5,2) CHECK (attendance_pct BETWEEN 0 AND 100),
    status         smallint NOT NULL CHECK (status IN (1, 2, 3, 4)),  -- 1 cursando, 2 aprovado, 3 reprovado, 4 trancado
    UNIQUE (student_id, class_id)
);

CREATE INDEX enrollments_class_idx ON enrollments (class_id);

-- Mesma estrutura da V1. Recebe os dados pelo pg_dump.
CREATE TABLE holidays (
    id           serial PRIMARY KEY,
    holiday_date date NOT NULL,
    description  varchar(100) NOT NULL
);

-- Para a conferência de índices.
CREATE EXTENSION IF NOT EXISTS amcheck;
