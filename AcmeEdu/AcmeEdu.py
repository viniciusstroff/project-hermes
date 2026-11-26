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
from core.compatible import CopyEntity
from core.multi_target_migration import MultiTargetMigration, TargetMigration
from core.queries import SourceQuery
from core.references import ReferenceMap
from core.strategies import DateConvert, Fixed, Lookup, Rename, Transform
from core.table_migration import TableMigration
from core.tasks import task

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

class TeacherPeople:
    """Pessoa de cada professor: o aluno com o mesmo CPF (só dígitos) ou 100000 + cod_prof.

    Os alunos chegam em ordem de cod_aluno. Quando dois alunos têm o mesmo
    CPF, vale o de menor cod_aluno. O agrupamento é feito em Python para o
    script rodar com a V1 em qualquer banco.
    """

    def __init__(self, students):
        self._by_cpf = {}
        for row in students:
            digits = only_digits(row['cpf'])
            if digits and digits not in self._by_cpf:
                self._by_cpf[digits] = (row['cod_aluno'], row['fone'])

    def _student(self, row):
        return self._by_cpf.get(only_digits(row['cpf']))

    def is_student(self, row):
        return self._student(row) is not None

    def person_id(self, row):
        student = self._student(row)
        return student[0] if student else TEACHER_PERSON_OFFSET + row['cod_prof']

    def student_phone(self, row):
        student = self._student(row)
        return student[1] if student else None


def _has_student_phone(row):
    phone = clean_phone(row['fone'])
    return phone is not None


def _teacher_phone_is_new(column, people):
    """Telefone do professor que ainda não está em phones para a mesma pessoa."""

    def condition(row):
        phone = clean_phone(row[column])
        if phone is None:
            return False
        if people.is_student(row) and phone == clean_phone(people.student_phone(row)):
            return False
        if column == 'fone2' and phone == clean_phone(row['fone1']):
            return False
        return True

    return condition


class Migracao(BaseMigration):
    """Migração V1 -> V2 da AcmeEdu."""

    def __init__(self):
        super().__init__(_clientdir)

    # Fase de preparação (01_prepare_target) ------------------------------

    def compatible_entities(self):
        # Os feriados já estão no padrão da V2: passam sem transformação.
        return [CopyEntity('holidays', source='v1', order_by='id')]

    def declare_reference_maps(self):
        # Lido da V2 antes da primeira fase, junto com os outros mapas.
        return [ReferenceMap('student_statuses', table='student_statuses', key='code', value='id')]

    def run_initial(self):
        self.comment('LIMPEZA DAS TABELAS DA V2')
        self.clear([*reversed(TARGET_TABLES), 'holidays'])
        self.comment('DESABILITAR TRIGGERS DE TABELAS')
        # Todas as tabelas do destino; o runner reabilita na fase final.
        self.suspend_constraints()

    # Fase de carga (03_load_transformed_data) ----------------------------
    #
    # Cada carga é uma tarefa. Nenhuma depende do resultado de outra, então
    # todas ficam na sequência 1 e rodam juntas com --workers maior que 1. A
    # ordem de declaração é a ordem dos comandos no 03_load_transformed_data.

    @task('courses')
    def insert_courses(self):
        self.print_log('Inserindo cursos')
        self.comment('COURSES')
        TableMigration(
            source=SourceQuery('tb_curso', order_by='cod_curso'),
            target='courses',
            fields=[
                Rename('cod_curso', 'id'),
                Transform('nome_curso', _column(normalize_name), v2_name='name'),
                Rename('sigla', 'code'),
                Lookup('modalidade', MODALITIES, v2_name='modality', key_normalizer=_upper_key),
                Rename('carga_horaria', 'workload_hours'),
                Transform('ativo', _column(is_active), v2_name='active'),
            ],
        ).run(self.context.source('v1'))

    @task('students')
    def insert_students(self):
        """Cada aluno vira uma pessoa, um aluno, um telefone e um responsável."""

        self.print_log('Inserindo alunos, pessoas, telefones e responsáveis')
        self.comment('PEOPLE, STUDENTS, PHONES E GUARDIANS (ALUNOS)')
        MultiTargetMigration(
            source=SourceQuery('tb_aluno', order_by='cod_aluno'),
            label='students',
            targets=[
                TargetMigration('people', self._student_person_fields()),
                TargetMigration('students', [
                    Rename('cod_aluno', 'id'),
                    Rename('cod_aluno', 'person_id'),
                    Rename('cod_curso', 'course_id'),
                    Lookup('situacao', self.student_status_ids(), v2_name='status_id', key_normalizer=_upper_key),
                    Transform('obs', _column(clean_text), v2_name='notes'),
                ]),
                TargetMigration('phones', [
                    Rename('cod_aluno', 'person_id'),
                    Transform('fone', _column(clean_phone), v2_name='number'),
                    Fixed('source', 'student'),
                ], condition=_has_student_phone),
                TargetMigration('guardians', [
                    Rename('cod_aluno', 'student_id'),
                    Transform('nome_resp', _column(normalize_name), v2_name='name'),
                    Transform('fone_resp', _column(clean_phone), v2_name='phone'),
                    Transform('parentesco', _column(relationship_of), v2_name='relationship'),
                ], condition=lambda row: normalize_name(row['nome_resp']) is not None),
            ],
        ).run(self.context.source('v1'))

    @task('teachers')
    def insert_teachers(self):
        """Professores que já são alunos reaproveitam a pessoa do aluno."""

        self.print_log('Inserindo professores')
        engine = self.context.source('v1')
        people = TeacherPeople(engine.read(SourceQuery('tb_aluno', fields=('cod_aluno', 'cpf', 'fone'), order_by='cod_aluno')))
        person_id = Transform('cod_prof', lambda value, row: people.person_id(row), v2_name='person_id')
        self.comment('PEOPLE, TEACHERS E PHONES (PROFESSORES)')
        MultiTargetMigration(
            source=SourceQuery('tb_professor', order_by='cod_prof'),
            label='teachers',
            targets=[
                TargetMigration('people', [
                    Transform('cod_prof', lambda value, row: people.person_id(row), v2_name='id'),
                    Transform('nome_prof', _column(normalize_name), v2_name='name'),
                    Transform('cpf', _column(clean_cpf)),
                    Transform('email', _column(clean_email)),
                ], condition=lambda row: not people.is_student(row)),
                TargetMigration('teachers', [
                    Rename('cod_prof', 'id'),
                    person_id,
                    Transform('titulacao', _column(degree_of), v2_name='degree'),
                ]),
                TargetMigration('phones', [
                    person_id,
                    Transform('fone1', _column(clean_phone), v2_name='number'),
                    Fixed('source', 'teacher'),
                ], condition=_teacher_phone_is_new('fone1', people)),
                TargetMigration('phones', [
                    person_id,
                    Transform('fone2', _column(clean_phone), v2_name='number'),
                    Fixed('source', 'teacher'),
                ], condition=_teacher_phone_is_new('fone2', people)),
            ],
        ).run(self.context.source('v1'))

    @task('subjects')
    def insert_subjects(self):
        self.print_log('Inserindo disciplinas')
        self.comment('SUBJECTS')
        TableMigration(
            source=SourceQuery('tb_disciplina', order_by='cod_disc'),
            target='subjects',
            fields=[
                Rename('cod_disc', 'id'),
                Transform('nome_disc', _column(clean_text), v2_name='name'),
                Rename('cod_curso', 'course_id'),
                Rename('cod_prof', 'teacher_id'),
                Rename('carga_horaria', 'workload_hours'),
            ],
        ).run(self.context.source('v1'))

    @task('classes')
    def insert_classes(self):
        self.print_log('Inserindo turmas')
        self.comment('CLASSES')
        TableMigration(
            source=SourceQuery('tb_turma', order_by='cod_turma'),
            target='classes',
            fields=[
                Rename('cod_turma', 'id'),
                Rename('cod_disc', 'subject_id'),
                Transform('ano_semestre', _column(term_year), v2_name='year'),
                Transform('ano_semestre', _column(term_number), v2_name='term'),
                Transform('sala', _column(clean_text), v2_name='room'),
            ],
        ).run(self.context.source('v1'))

    @task('enrollments')
    def insert_enrollments(self):
        self.print_log('Inserindo matrículas')
        self.comment('ENROLLMENTS')
        TableMigration(
            source=SourceQuery('tb_matricula', order_by='cod_matricula'),
            target='enrollments',
            fields=[
                Rename('cod_matricula', 'id'),
                Rename('cod_aluno', 'student_id'),
                Rename('cod_turma', 'class_id'),
                DateConvert('dt_matricula', '%Y%m%d', v2_name='enrolled_at'),
                Rename('nota_final', 'final_grade'),
                Transform('frequencia', _column(attendance_pct), v2_name='attendance_pct'),
                Lookup('status', ENROLLMENT_STATUSES, key_normalizer=_upper_key),
            ],
        ).run(self.context.source('v1'))

    # Fase final (04_finalize_target) -------------------------------------

    def reset_sequences(self):
        self.print_log('Ajustando sequences')
        self.reset_identity([*TARGET_TABLES, 'holidays'], column='id')

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
