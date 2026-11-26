"""Confere a carga da AcmeEdu comparando a V1 com a V2, pelos adapters.

Uso, dentro do container project-hermes:

    python3 AcmeEdu/conferir.py

As conexões vêm do AcmeEdu/.env, como na migração: SOURCE_V1_* e TARGET_*.
Variáveis de ambiente com esses prefixos sobrescrevem o .env, por exemplo fora
do Docker:

    SOURCE_V1_HOST=localhost SOURCE_V1_PORT=5441 TARGET_HOST=localhost TARGET_PORT=5442 python3 AcmeEdu/conferir.py

A V1 e a V2 podem estar em qualquer banco com adapter: o script lê as duas por
SourceQuery. O esperado é calculado a partir da V1, com as mesmas funções de
limpeza do AcmeEdu.py. Sequences e amcheck só são conferidos quando o destino
é PostgreSQL. Cada verificação mostra o esperado e o encontrado. O script sai
com código 1 se alguma falhar.
"""

import os
import sys
from collections import Counter
from dataclasses import dataclass

_clientdir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(_clientdir))

from dotenv import dotenv_values

from core.adapters.registry import connect_adapter
from core.connections import parse_connections
from core.queries import NativeQuery, SourceQuery

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


def open_adapters():
    """Adapters da origem v1 e do destino, como a migração abre."""

    overrides = {key: value for key, value in os.environ.items() if key.startswith(('SOURCE_', 'TARGET_'))}
    config = {**dotenv_values(os.path.join(_clientdir, '.env')), **overrides}
    sources, target = parse_connections(config)
    return connect_adapter(sources['v1'], 'source'), connect_adapter(target, 'target')


def read(adapter, entity, *fields, order_by=()):
    return list(adapter.read(SourceQuery(entity, fields=fields, order_by=order_by)))


def pairs(adapter, entity, key, value):
    return {row[key]: row[value] for row in read(adapter, entity, key, value)}


def expected_people_and_phones(v1):
    """Refaz na memória o agrupamento de pessoas e telefones a partir da V1."""

    people = {}
    phones = set()
    student_by_cpf = {}

    for row in read(v1, 'tb_aluno', 'cod_aluno', 'nome_aluno', 'cpf', 'email_contato', 'fone', order_by='cod_aluno'):
        cod_aluno = row['cod_aluno']
        people[cod_aluno] = (normalize_name(row['nome_aluno']), clean_cpf(row['cpf']), clean_email(row['email_contato']))
        digits = clean_cpf(row['cpf'])
        if digits:
            student_by_cpf.setdefault(digits, cod_aluno)
        if clean_phone(row['fone']):
            phones.add((cod_aluno, clean_phone(row['fone'])))

    teacher_person = {}
    for row in read(v1, 'tb_professor', 'cod_prof', 'nome_prof', 'cpf', 'email', 'fone1', 'fone2', order_by='cod_prof'):
        student = student_by_cpf.get(clean_cpf(row['cpf']))
        person_id = student if student else TEACHER_PERSON_OFFSET + row['cod_prof']
        teacher_person[row['cod_prof']] = person_id
        if not student:
            people[person_id] = (normalize_name(row['nome_prof']), clean_cpf(row['cpf']), clean_email(row['email']))
        for fone in (row['fone1'], row['fone2']):
            if clean_phone(fone):
                phones.add((person_id, clean_phone(fone)))

    return people, phones, teacher_person


def count_checks(v1, v2, people, phones):
    expected = {
        'courses': len(read(v1, 'tb_curso', 'cod_curso')),
        'people': len(people),
        'students': len(read(v1, 'tb_aluno', 'cod_aluno')),
        'teachers': len(read(v1, 'tb_professor', 'cod_prof')),
        'phones': len(phones),
        'guardians': sum(1 for row in read(v1, 'tb_aluno', 'nome_resp') if normalize_name(row['nome_resp'])),
        'subjects': len(read(v1, 'tb_disciplina', 'cod_disc')),
        'classes': len(read(v1, 'tb_turma', 'cod_turma')),
        'enrollments': len(read(v1, 'tb_matricula', 'cod_matricula')),
        'holidays': len(read(v1, 'holidays', 'id')),
    }
    return [
        Check(f'linhas em {table}', count, len(read(v2, table, 'id')))
        for table, count in expected.items()
    ]


def content_checks(v1, v2, people, phones, teacher_person):
    found_people = {
        row['id']: (row['name'], row['cpf'].strip() if row['cpf'] else None, row['email'])
        for row in read(v2, 'people', 'id', 'name', 'cpf', 'email')
    }
    found_phones = {(row['person_id'], row['number']) for row in read(v2, 'phones', 'person_id', 'number')}
    found_teachers = pairs(v2, 'teachers', 'id', 'person_id')
    shared = {teacher: person for teacher, person in teacher_person.items() if person < TEACHER_PERSON_OFFSET}

    v1_notes = {cod: clean_text(obs) for cod, obs in pairs(v1, 'tb_aluno', 'cod_aluno', 'obs').items()}
    v2_notes = pairs(v2, 'students', 'id', 'notes')
    backslash_notes = {cod for cod, obs in v1_notes.items() if obs and '\\' in obs}
    names = [name for name, _, _ in found_people.values()]
    cpfs = Counter(cpf for _, cpf, _ in found_people.values() if cpf)

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
              sum(1 for name in names if name and (name != name.strip() or '  ' in name))),
        Check('CPF repetido em people', 0, sum(1 for count in cpfs.values() if count > 1)),
    ]


def foreign_key_checks(v2):
    checks = []
    for table, column, parent in FOREIGN_KEYS:
        parent_ids = {row['id'] for row in read(v2, parent, 'id')}
        orphans = sum(1 for row in read(v2, table, column) if row[column] is not None and row[column] not in parent_ids)
        checks.append(Check(f'{table}.{column} sem órfãos', 0, orphans))
    return checks


def postgres_checks(v2):
    """Sequences e amcheck: só existem quando o destino é PostgreSQL."""

    def native(sql):
        return list(v2.read(NativeQuery('postgres', sql)))

    checks = []
    for table in [*TARGET_TABLES, 'holidays']:
        highest = native(f'SELECT coalesce(max(id), 0) AS highest FROM public.{table}')[0]['highest']
        last_value = native(f'SELECT last_value FROM public.{table}_id_seq')[0]['last_value']
        checks.append(Check(f'{table}_id_seq >= maior id', True, last_value >= highest))

    indexes = native(
        "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_am a ON a.oid = c.relam "
        "WHERE n.nspname = 'public' AND a.amname = 'btree' ORDER BY c.relname"
    )
    for row in indexes:
        index = row['relname']
        try:
            # heapallindexed: toda linha da tabela precisa estar no índice.
            native(f"SELECT bt_index_check('public.{index}'::regclass, true)")
            result = 'consistente'
        except Exception as exc:
            # Um erro aborta a transação de leitura; as checagens seguintes precisam de outra.
            v2.connection.rollback()
            result = str(exc).strip().splitlines()[0]
        checks.append(Check(f'índice {index}', 'consistente', result))
    return checks


def main():
    v1, v2 = open_adapters()
    print(f'Origem v1: {v1.name} {v1.server_version or ""}'.rstrip())
    print(f'Destino: {v2.name} {v2.server_version or ""}'.rstrip())
    people, phones, teacher_person = expected_people_and_phones(v1)

    checks = [
        *count_checks(v1, v2, people, phones),
        *content_checks(v1, v2, people, phones, teacher_person),
        *foreign_key_checks(v2),
        *(postgres_checks(v2) if v2.name == 'postgres' else []),
    ]
    v1.close()
    v2.close()

    width = max(len(check.name) for check in checks)
    for check in checks:
        status = 'ok    ' if check.ok else 'FALHOU'
        print(f'{status} {check.name:<{width}}  esperado: {check.expected}  encontrado: {check.found}')

    failed = [check for check in checks if not check.ok]
    print(f'\n{len(checks) - len(failed)} de {len(checks)} verificações passaram.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
