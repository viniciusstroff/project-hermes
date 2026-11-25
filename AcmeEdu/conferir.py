"""Confere a carga da AcmeEdu comparando a V1 com a V2.

Uso, dentro do container project-hermes:

    python3 AcmeEdu/conferir.py

Fora do Docker, sobrescreva o host e a porta pelas variáveis de ambiente:

    PG_V1_HOST=localhost PG_V1_PORT=5441 PG_V2_HOST=localhost PG_V2_PORT=5442 python3 AcmeEdu/conferir.py

O esperado é calculado a partir da V1, com as mesmas funções de limpeza do
AcmeEdu.py. Cada verificação mostra o esperado e o encontrado. O script sai com
código 1 se alguma falhar.
"""

import os
import sys
from dataclasses import dataclass

_clientdir = os.path.dirname(os.path.realpath(__file__))

import psycopg2
from dotenv import dotenv_values

# Rodando como script, a pasta AcmeEdu/ está no sys.path e AcmeEdu é o AcmeEdu.py.
from AcmeEdu import (
    TARGET_TABLES,
    TEACHER_PERSON_OFFSET,
    clean_cpf,
    clean_email,
    clean_phone,
    clean_text,
    normalize_name,
)

FOREIGN_KEYS = [
    ('students', 'person_id', 'people'),
    ('students', 'course_id', 'courses'),
    ('students', 'status_id', 'student_statuses'),
    ('teachers', 'person_id', 'people'),
    ('phones', 'person_id', 'people'),
    ('guardians', 'student_id', 'students'),
    ('subjects', 'course_id', 'courses'),
    ('subjects', 'teacher_id', 'teachers'),
    ('classes', 'subject_id', 'subjects'),
    ('enrollments', 'student_id', 'students'),
    ('enrollments', 'class_id', 'classes'),
]


@dataclass
class Check:
    name: str
    expected: object
    found: object

    @property
    def ok(self):
        return self.expected == self.found


def connect(prefix):
    config = {**dotenv_values(os.path.join(_clientdir, '.env')), **os.environ}
    connection = psycopg2.connect(
        host=config[f'{prefix}_HOST'],
        port=config[f'{prefix}_PORT'],
        dbname=config[f'{prefix}_NAME'],
        user=config[f'{prefix}_USER'],
        password=config[f'{prefix}_PASS'],
    )
    # Só leitura. Com autocommit, um índice com erro no amcheck não aborta as consultas seguintes.
    connection.autocommit = True
    return connection


def rows(connection, sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchall()


def scalar(connection, sql):
    return rows(connection, sql)[0][0]


def expected_people_and_phones(v1):
    """Refaz na memória o agrupamento de pessoas e telefones a partir da V1."""

    people = {}
    phones = set()
    student_by_cpf = {}
    student_phone = {}

    for cod_aluno, nome, cpf, email, fone in rows(
        v1, 'SELECT cod_aluno, nome_aluno, cpf, email_contato, fone FROM tb_aluno ORDER BY cod_aluno'
    ):
        people[cod_aluno] = (normalize_name(nome), clean_cpf(cpf), clean_email(email))
        digits = clean_cpf(cpf)
        if digits:
            student_by_cpf.setdefault(digits, cod_aluno)
        student_phone[cod_aluno] = clean_phone(fone)
        if clean_phone(fone):
            phones.add((cod_aluno, clean_phone(fone)))

    teacher_person = {}
    for cod_prof, nome, cpf, email, fone1, fone2 in rows(
        v1, 'SELECT cod_prof, nome_prof, cpf, email, fone1, fone2 FROM tb_professor ORDER BY cod_prof'
    ):
        student = student_by_cpf.get(clean_cpf(cpf))
        person_id = student if student else TEACHER_PERSON_OFFSET + cod_prof
        teacher_person[cod_prof] = person_id
        if not student:
            people[person_id] = (normalize_name(nome), clean_cpf(cpf), clean_email(email))
        for fone in (fone1, fone2):
            if clean_phone(fone):
                phones.add((person_id, clean_phone(fone)))

    return people, phones, teacher_person


def count_checks(v1, v2, people, phones):
    expected = {
        'courses': scalar(v1, 'SELECT count(*) FROM tb_curso'),
        'people': len(people),
        'students': scalar(v1, 'SELECT count(*) FROM tb_aluno'),
        'teachers': scalar(v1, 'SELECT count(*) FROM tb_professor'),
        'phones': len(phones),
        'guardians': sum(1 for (nome,) in rows(v1, 'SELECT nome_resp FROM tb_aluno') if normalize_name(nome)),
        'subjects': scalar(v1, 'SELECT count(*) FROM tb_disciplina'),
        'classes': scalar(v1, 'SELECT count(*) FROM tb_turma'),
        'enrollments': scalar(v1, 'SELECT count(*) FROM tb_matricula'),
        'holidays': scalar(v1, 'SELECT count(*) FROM holidays'),
    }
    return [
        Check(f'linhas em {table}', count, scalar(v2, f'SELECT count(*) FROM public.{table}'))
        for table, count in expected.items()
    ]


def content_checks(v1, v2, people, phones, teacher_person):
    found_people = {
        person_id: (name, cpf.strip() if cpf else None, email)
        for person_id, name, cpf, email in rows(v2, 'SELECT id, name, cpf, email FROM public.people')
    }
    found_phones = set(rows(v2, 'SELECT person_id, number FROM public.phones'))
    found_teachers = dict(rows(v2, 'SELECT id, person_id FROM public.teachers'))
    shared = {teacher: person for teacher, person in teacher_person.items() if person < TEACHER_PERSON_OFFSET}

    v1_notes = {cod: clean_text(obs) for cod, obs in rows(v1, 'SELECT cod_aluno, obs FROM tb_aluno')}
    v2_notes = dict(rows(v2, 'SELECT id, notes FROM public.students'))
    backslash_notes = {cod for cod, obs in v1_notes.items() if obs and '\\' in obs}

    return [
        Check('pessoas com nome, CPF e e-mail esperados', 0,
              sum(1 for key, value in people.items() if found_people.get(key) != value)),
        Check('telefones por pessoa iguais ao esperado', 0, len(phones ^ found_phones)),
        Check('professores que são alunos usando a pessoa do aluno', len(shared),
              sum(1 for teacher, person in shared.items() if found_teachers.get(teacher) == person)),
        Check('observações com barra invertida iguais às da V1', len(backslash_notes),
              sum(1 for cod in backslash_notes if v2_notes.get(cod) == v1_notes[cod])),
        Check('observações iguais às da V1', 0,
              sum(1 for cod, obs in v1_notes.items() if v2_notes.get(cod) != obs)),
        Check('nomes com espaço sobrando', 0,
              scalar(v2, "SELECT count(*) FROM public.people WHERE name <> btrim(name) OR name LIKE '%  %'")),
        Check('CPF repetido em people', 0,
              scalar(v2, 'SELECT count(*) FROM (SELECT cpf FROM public.people WHERE cpf IS NOT NULL '
                         'GROUP BY cpf HAVING count(*) > 1) repetidos')),
    ]


def foreign_key_checks(v2):
    return [
        Check(f'{table}.{column} sem órfãos', 0, scalar(v2,
              f'SELECT count(*) FROM public.{table} t WHERE t.{column} IS NOT NULL '
              f'AND NOT EXISTS (SELECT 1 FROM public.{parent} p WHERE p.id = t.{column})'))
        for table, column, parent in FOREIGN_KEYS
    ]


def sequence_checks(v2):
    checks = []
    for table in [*TARGET_TABLES, 'holidays']:
        highest = scalar(v2, f'SELECT coalesce(max(id), 0) FROM public.{table}')
        last_value = scalar(v2, f'SELECT last_value FROM public.{table}_id_seq')
        checks.append(Check(f'{table}_id_seq >= maior id', True, last_value >= highest))
    return checks


def index_checks(v2):
    """amcheck com heapallindexed: toda linha da tabela precisa estar no índice."""

    checks = []
    for (index,) in rows(v2, """
        SELECT c.relname
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_am a ON a.oid = c.relam
        WHERE n.nspname = 'public' AND a.amname = 'btree'
        ORDER BY c.relname
    """):
        try:
            rows(v2, f"SELECT bt_index_check('public.{index}'::regclass, true)")
            result = 'consistente'
        except psycopg2.Error as exc:
            result = str(exc).strip().splitlines()[0]
        checks.append(Check(f'índice {index}', 'consistente', result))
    return checks


def main():
    v1 = connect('PG_V1')
    v2 = connect('PG_V2')
    people, phones, teacher_person = expected_people_and_phones(v1)

    checks = [
        *count_checks(v1, v2, people, phones),
        *content_checks(v1, v2, people, phones, teacher_person),
        *foreign_key_checks(v2),
        *sequence_checks(v2),
        *index_checks(v2),
    ]

    width = max(len(check.name) for check in checks)
    for check in checks:
        status = 'ok    ' if check.ok else 'FALHOU'
        print(f'{status} {check.name:<{width}}  esperado: {check.expected}  encontrado: {check.found}')

    failed = [check for check in checks if not check.ok]
    print(f'\n{len(checks) - len(failed)} de {len(checks)} verificações passaram.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
