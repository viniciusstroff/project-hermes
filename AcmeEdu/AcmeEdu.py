"""Migração da AcmeEdu, instituição de ensino fictícia, da V1 legada para a V2.

As bases ficam em AcmeEdu/docker-compose.yml e o mapeamento está em
AcmeEdu/README.md. As transformações próprias do cliente são funções puras no
início do arquivo, testadas em tests/test_acmeedu.py.
"""

import os
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation

_clientdir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(_clientdir))

from core.base_migration import BaseMigration
from core.multi_target_migration import MultiTargetMigration, TargetMigration
from core.references import ReferenceMap
from core.strategies import DateConvert, Fixed, Lookup, Rename, Transform
from core.table_migration import TableMigration

# Professores que não são alunos ganham em people o id 100000 + cod_prof.
# Os alunos usam o próprio cod_aluno, que na V1 fica abaixo disso.
TEACHER_PERSON_OFFSET = 100000

NAME_PARTICLES = {'da', 'de', 'do', 'das', 'dos', 'e'}
MODALITIES = {'P': 1, 'E': 2}
STUDENT_STATUS_CODES = {'A': 'active', 'T': 'on_hold', 'F': 'graduated', 'C': 'cancelled'}
ENROLLMENT_STATUSES = {'M': 1, 'A': 2, 'R': 3, 'T': 4}
DEGREES = {
    'especialista': 1,
    'especializacao': 1,
    'mestre': 2,
    'mestrado': 2,
    'doutor': 3,
    'doutora': 3,
    'doutorado': 3,
    'dr': 3,
    'dra': 3,
}
RELATIONSHIPS = {'mae': 'Mãe', 'pai': 'Pai', 'avo': 'Avó', 'tio': 'Tio', 'tia': 'Tia'}

_EMAIL = re.compile(r'[\w.\-+]+@[\w\-]+(?:\.[\w\-]+)+')
_TERM = re.compile(r'^\s*(\d{4})\s*/\s*([12])\s*$')


# ---------------------------------------------------------------------------
# Transformações do cliente
# ---------------------------------------------------------------------------


def normalize_name(value):
    """Primeira letra de cada palavra em maiúscula, partículas em minúscula."""

    words = (value or '').split()
    if not words:
        return None
    result = []
    for index, word in enumerate(words):
        lower = word.lower()
        if index > 0 and lower in NAME_PARTICLES:
            result.append(lower)
        else:
            # D'ávila -> D'Ávila
            result.append("'".join(part[:1].upper() + part[1:] for part in lower.split("'")))
    return ' '.join(result)


def only_digits(value):
    digits = re.sub(r'\D', '', value or '')
    return digits or None


def clean_cpf(value):
    digits = only_digits(value)
    return digits if digits and len(digits) == 11 else None


def clean_phone(value):
    """DDD e número, sem o +55. Devolve None se não sobrar um telefone válido."""

    digits = only_digits(value)
    if digits and len(digits) in (12, 13) and digits.startswith('55'):
        digits = digits[2:]
    return digits if digits and len(digits) in (10, 11) else None


def clean_email(value):
    match = _EMAIL.search(value or '')
    return match.group(0).lower() if match else None


def clean_gender(value):
    gender = (value or '').strip().upper()
    return gender if gender in ('F', 'M') else None


def clean_text(value):
    text = (value or '').strip()
    return text or None


def is_active(value):
    return (value or '').strip().upper() == 'S'


def degree_of(value):
    key = _strip_accents((value or '').strip().lower()).rstrip('.')
    return DEGREES.get(key)


def relationship_of(value):
    key = _strip_accents((value or '').strip().lower())
    if not key:
        return None
    return RELATIONSHIPS.get(key, normalize_name(value))


def term_year(value):
    match = _TERM.match(value or '')
    return int(match.group(1)) if match else None


def term_number(value):
    match = _TERM.match(value or '')
    return int(match.group(2)) if match else None


def attendance_pct(value):
    text = (value or '').strip().rstrip('%').strip()
    if not text:
        return None
    try:
        return Decimal(text).quantize(Decimal('0.01'))
    except InvalidOperation:
        return None


def _strip_accents(text):
    return ''.join(
        char for char in unicodedata.normalize('NFKD', text)
        if not unicodedata.combining(char)
    )


def _column(function):
    """Adapta uma função de um valor à assinatura (value, row) do Transform."""

    return lambda value, row: function(value)


def _upper_key(value):
    return str(value).strip().upper()


# ---------------------------------------------------------------------------
# Migração
# ---------------------------------------------------------------------------

LEGACY_TABLES = ['tb_curso', 'tb_professor', 'tb_disciplina', 'tb_turma', 'tb_aluno', 'tb_matricula', 'tb_log_acesso']
LEGACY_SEQUENCES = [
    'tb_curso_cod_curso_seq',
    'tb_professor_cod_prof_seq',
    'tb_disciplina_cod_disc_seq',
    'tb_turma_cod_turma_seq',
    'tb_aluno_cod_aluno_seq',
    'tb_matricula_cod_matricula_seq',
    'tb_log_acesso_cod_log_seq',
]
TARGET_TABLES = [
    'courses',
    'people',
    'students',
    'teachers',
    'phones',
    'guardians',
    'subjects',
    'classes',
    'enrollments',
]

STUDENTS_SQL = """
SELECT cod_aluno, nome_aluno, dt_nasc, cpf, fone, email_contato, sexo, situacao, cod_curso,
       nome_resp, fone_resp, parentesco, obs, dt_cadastro
FROM public.tb_aluno
ORDER BY cod_aluno {limit}
"""

# person_id: o cod_aluno de um aluno com o mesmo CPF (só dígitos) ou
# 100000 + cod_prof. is_student diz se a pessoa já veio pelos alunos.
TEACHERS_SQL = r"""
SELECT p.cod_prof, p.nome_prof, p.cpf, p.email, p.titulacao, p.fone1, p.fone2,
       COALESCE(a.cod_aluno, {offset} + p.cod_prof) AS person_id,
       a.cod_aluno IS NOT NULL AS is_student,
       a.fone AS student_phone
FROM public.tb_professor p
LEFT JOIN LATERAL (
    SELECT al.cod_aluno, al.fone
    FROM public.tb_aluno al
    WHERE regexp_replace(p.cpf, '\D', '', 'g') <> ''
      AND regexp_replace(al.cpf, '\D', '', 'g') = regexp_replace(p.cpf, '\D', '', 'g')
    ORDER BY al.cod_aluno
    LIMIT 1
) a ON true
ORDER BY p.cod_prof {limit}
"""


def _has_student_phone(row):
    phone = clean_phone(row['fone'])
    return phone is not None


def _teacher_phone_is_new(column):
    """Telefone do professor que ainda não está em phones para a mesma pessoa."""

    def condition(row):
        phone = clean_phone(row[column])
        if phone is None:
            return False
        if row['is_student'] and phone == clean_phone(row['student_phone']):
            return False
        if column == 'fone2' and phone == clean_phone(row['fone1']):
            return False
        return True

    return condition


class Migracao(BaseMigration):
    """Migração V1 -> V2 da AcmeEdu."""

    def __init__(self):
        super().__init__(_clientdir)

    # Fase de preparação (01_prepare_target.sql) ------------------------------

    def dump_ignoring_tables(self):
        return [f'public.{name}' for name in (*LEGACY_TABLES, *LEGACY_SEQUENCES)]

    def dump_non_existing_tables(self):
        return []

    def declare_reference_maps(self):
        # Lido da V2 antes da primeira fase, junto com os outros mapas.
        return [ReferenceMap('student_statuses', table='public.student_statuses', key='code', value='id')]

    def set_indexes_commands(self):
        # Mesmo uso do checklist de novo cliente: nomes de índice.
        self.set_index_commands_for(['people_name_idx', 'enrollments_class_idx'])

    def run_initial(self):
        self.print_comment('LIMPEZA DAS TABELAS DA V2')
        tables = ', '.join(f'public.{name}' for name in [*reversed(TARGET_TABLES), 'holidays'])
        self.write_sql(f'TRUNCATE {tables} RESTART IDENTITY;\n')
        self.print_comment('DESABILITAR TRIGGERS DE TABELAS')
        self.write_sql(self.disable_triggers)
        self.print_comment('DESABILITAR ÍNDICES DE TABELAS')
        self.write_sql(self.disable_indexes)

    # Fase de carga (03_load_transformed_data.sql) ----------------------------

    def run_inserts(self):
        self.insert_courses()
        self.insert_students()
        self.insert_teachers()
        self.insert_subjects()
        self.insert_classes()
        self.insert_enrollments()

    def insert_courses(self):
        self.print_log('Inserindo cursos')
        self.print_comment('COURSES')
        TableMigration(
            source_sql='SELECT {fields} FROM public.tb_curso ORDER BY cod_curso {limit}',
            target='public.courses',
            fields=[
                Rename('cod_curso', 'id'),
                Transform('nome_curso', _column(normalize_name), v2_name='name'),
                Rename('sigla', 'code'),
                Lookup('modalidade', MODALITIES, v2_name='modality', key_normalizer=_upper_key),
                Rename('carga_horaria', 'workload_hours'),
                Transform('ativo', _column(is_active), v2_name='active'),
            ],
        ).run(self.context.engine('pg_v1'))

    def insert_students(self):
        """Cada aluno vira uma pessoa, um aluno, um telefone e um responsável."""

        self.print_log('Inserindo alunos, pessoas, telefones e responsáveis')
        self.print_comment('PEOPLE, STUDENTS, PHONES E GUARDIANS (ALUNOS)')
        MultiTargetMigration(
            source_sql=STUDENTS_SQL,
            label='public.students',
            targets=[
                TargetMigration('public.people', self._student_person_fields()),
                TargetMigration('public.students', [
                    Rename('cod_aluno', 'id'),
                    Rename('cod_aluno', 'person_id'),
                    Rename('cod_curso', 'course_id'),
                    Lookup('situacao', self.student_status_ids(), v2_name='status_id', key_normalizer=_upper_key),
                    Transform('obs', _column(clean_text), v2_name='notes'),
                ]),
                TargetMigration('public.phones', [
                    Rename('cod_aluno', 'person_id'),
                    Transform('fone', _column(clean_phone), v2_name='number'),
                    Fixed('source', 'student'),
                ], condition=_has_student_phone),
                TargetMigration('public.guardians', [
                    Rename('cod_aluno', 'student_id'),
                    Transform('nome_resp', _column(normalize_name), v2_name='name'),
                    Transform('fone_resp', _column(clean_phone), v2_name='phone'),
                    Transform('parentesco', _column(relationship_of), v2_name='relationship'),
                ], condition=lambda row: normalize_name(row['nome_resp']) is not None),
            ],
        ).run(self.context.engine('pg_v1'))

    def insert_teachers(self):
        """Professores que já são alunos reaproveitam a pessoa do aluno."""

        self.print_log('Inserindo professores')
        self.print_comment('PEOPLE, TEACHERS E PHONES (PROFESSORES)')
        MultiTargetMigration(
            source_sql=TEACHERS_SQL.replace('{offset}', str(TEACHER_PERSON_OFFSET)),
            label='public.teachers',
            targets=[
                TargetMigration('public.people', [
                    Rename('person_id', 'id'),
                    Transform('nome_prof', _column(normalize_name), v2_name='name'),
                    Transform('cpf', _column(clean_cpf)),
                    Transform('email', _column(clean_email)),
                ], condition=lambda row: not row['is_student']),
                TargetMigration('public.teachers', [
                    Rename('cod_prof', 'id'),
                    Rename('person_id', 'person_id'),
                    Transform('titulacao', _column(degree_of), v2_name='degree'),
                ]),
                TargetMigration('public.phones', [
                    Rename('person_id', 'person_id'),
                    Transform('fone1', _column(clean_phone), v2_name='number'),
                    Fixed('source', 'teacher'),
                ], condition=_teacher_phone_is_new('fone1')),
                TargetMigration('public.phones', [
                    Rename('person_id', 'person_id'),
                    Transform('fone2', _column(clean_phone), v2_name='number'),
                    Fixed('source', 'teacher'),
                ], condition=_teacher_phone_is_new('fone2')),
            ],
        ).run(self.context.engine('pg_v1'))

    def insert_subjects(self):
        self.print_log('Inserindo disciplinas')
        self.print_comment('SUBJECTS')
        TableMigration(
            source_sql='SELECT {fields} FROM public.tb_disciplina ORDER BY cod_disc {limit}',
            target='public.subjects',
            fields=[
                Rename('cod_disc', 'id'),
                Transform('nome_disc', _column(clean_text), v2_name='name'),
                Rename('cod_curso', 'course_id'),
                Rename('cod_prof', 'teacher_id'),
                Rename('carga_horaria', 'workload_hours'),
            ],
        ).run(self.context.engine('pg_v1'))

    def insert_classes(self):
        self.print_log('Inserindo turmas')
        self.print_comment('CLASSES')
        TableMigration(
            source_sql='SELECT {fields} FROM public.tb_turma ORDER BY cod_turma {limit}',
            target='public.classes',
            fields=[
                Rename('cod_turma', 'id'),
                Rename('cod_disc', 'subject_id'),
                Transform('ano_semestre', _column(term_year), v2_name='year'),
                Transform('ano_semestre', _column(term_number), v2_name='term'),
                Transform('sala', _column(clean_text), v2_name='room'),
            ],
        ).run(self.context.engine('pg_v1'))

    def insert_enrollments(self):
        self.print_log('Inserindo matrículas')
        self.print_comment('ENROLLMENTS')
        TableMigration(
            source_sql='SELECT {fields} FROM public.tb_matricula ORDER BY cod_matricula {limit}',
            target='public.enrollments',
            fields=[
                Rename('cod_matricula', 'id'),
                Rename('cod_aluno', 'student_id'),
                Rename('cod_turma', 'class_id'),
                DateConvert('dt_matricula', '%Y%m%d', v2_name='enrolled_at'),
                Rename('nota_final', 'final_grade'),
                Transform('frequencia', _column(attendance_pct), v2_name='attendance_pct'),
                Lookup('status', ENROLLMENT_STATUSES, key_normalizer=_upper_key),
            ],
        ).run(self.context.engine('pg_v1'))

    # Fase final (04_finalize_target.sql) -------------------------------------

    def reset_sequences(self):
        self.print_log('Ajustando sequences')
        self.write_reset_sequences(TARGET_TABLES, column='id')

    # Apoio -------------------------------------------------------------------

    def student_status_ids(self):
        """Letra da V1 -> id de student_statuses, lido da V2."""

        ids_by_code = self.reference_map('student_statuses')
        missing = sorted(set(STUDENT_STATUS_CODES.values()) - set(ids_by_code))
        if missing:
            raise ValueError(f'student_statuses da V2 sem os códigos: {", ".join(missing)}')
        return {letter: ids_by_code[code] for letter, code in STUDENT_STATUS_CODES.items()}

    @staticmethod
    def _student_person_fields():
        return [
            Rename('cod_aluno', 'id'),
            Transform('nome_aluno', _column(normalize_name), v2_name='name'),
            Transform('cpf', _column(clean_cpf)),
            DateConvert('dt_nasc', v2_name='birth_date'),
            Transform('email_contato', _column(clean_email), v2_name='email'),
            Transform('sexo', _column(clean_gender), v2_name='gender'),
            Rename('dt_cadastro', 'created_at'),
        ]
