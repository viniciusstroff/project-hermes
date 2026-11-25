import datetime
import importlib
import sys
import types
import unittest
from decimal import Decimal

# Os stubs são os mesmos dos outros testes. O primeiro módulo importado na
# descoberta define o stub de todos, então ele precisa servir para todos.
psycopg2 = types.ModuleType('psycopg2')
psycopg2_extras = types.ModuleType('psycopg2.extras')
psycopg2_extras.RealDictCursor = object
psycopg2.extras = psycopg2_extras
psycopg2.connect = lambda *args, **kwargs: None
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)

firebirdsql = types.ModuleType('firebirdsql')
firebirdsql.connect = lambda *args, **kwargs: None
sys.modules.setdefault('firebirdsql', firebirdsql)


def dotenv_values(path):
    values = {}
    with open(path, encoding='utf-8') as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key] = value
    return values


dotenv = types.ModuleType('dotenv')
dotenv.dotenv_values = dotenv_values
sys.modules.setdefault('dotenv', dotenv)

from core.adapters.postgres import PostgresTargetAdapter
from core.base_migration import BaseMigration
from core.migration_engine import MigrationEngine

acmeedu = importlib.import_module('AcmeEdu.AcmeEdu')


class TransformationTest(unittest.TestCase):
    def test_normalize_name(self):
        cases = {
            "  LUCAS D'ÁVILA PEREIRA ": "Lucas D'Ávila Pereira",
            'Felipe  de Rocha Barros': 'Felipe de Rocha Barros',
            'ANA DA SILVA E SOUZA': 'Ana da Silva e Souza',
            'Análise e Desenvolvimento de Sistemas': 'Análise e Desenvolvimento de Sistemas',
            'pedagogia ': 'Pedagogia',
            'DA COSTA': 'Da Costa',
            '': None,
            '   ': None,
            None: None,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(acmeedu.normalize_name(value), expected)

    def test_clean_cpf(self):
        self.assertEqual(acmeedu.clean_cpf('100.002.100-00'), '10000210000')
        self.assertEqual(acmeedu.clean_cpf('10000210000'), '10000210000')
        self.assertIsNone(acmeedu.clean_cpf(''))
        self.assertIsNone(acmeedu.clean_cpf(None))
        self.assertIsNone(acmeedu.clean_cpf('123.456'))

    def test_clean_phone(self):
        self.assertEqual(acmeedu.clean_phone('(51) 90237-5700'), '51902375700')
        self.assertEqual(acmeedu.clean_phone('+55 61 90003-9595'), '61900039595')
        self.assertEqual(acmeedu.clean_phone('51902375700'), '51902375700')
        self.assertEqual(acmeedu.clean_phone('(11) 3333-4444'), '1133334444')
        self.assertIsNone(acmeedu.clean_phone(''))
        self.assertIsNone(acmeedu.clean_phone(None))
        self.assertIsNone(acmeedu.clean_phone('1234'))

    def test_clean_email(self):
        self.assertEqual(
            acmeedu.clean_email('Email: Aluno5@AcmeEdu.Example / tel. recado'),
            'aluno5@acmeedu.example',
        )
        self.assertEqual(acmeedu.clean_email('prof3@acmeedu.example'), 'prof3@acmeedu.example')
        self.assertIsNone(acmeedu.clean_email('sem e-mail'))
        self.assertIsNone(acmeedu.clean_email(''))
        self.assertIsNone(acmeedu.clean_email(None))

    def test_codes(self):
        self.assertEqual(acmeedu.clean_gender(' f'), 'F')
        self.assertIsNone(acmeedu.clean_gender(''))
        self.assertTrue(acmeedu.is_active('s'))
        self.assertFalse(acmeedu.is_active('N'))
        self.assertFalse(acmeedu.is_active(None))

    def test_degree_of(self):
        cases = {'MESTRE': 2, 'mestrado': 2, 'dra.': 3, 'Doutor': 3, 'Especialização': 1, '': None, None: None, 'Bacharel': None}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(acmeedu.degree_of(value), expected)

    def test_relationship_of(self):
        self.assertEqual(acmeedu.relationship_of('mae'), 'Mãe')
        self.assertEqual(acmeedu.relationship_of('Mãe'), 'Mãe')
        self.assertEqual(acmeedu.relationship_of('Avó'), 'Avó')
        self.assertEqual(acmeedu.relationship_of('PRIMA'), 'Prima')
        self.assertIsNone(acmeedu.relationship_of(''))

    def test_term(self):
        self.assertEqual((acmeedu.term_year('2024/1'), acmeedu.term_number('2024/1')), (2024, 1))
        self.assertEqual((acmeedu.term_year(' 2023 / 2 '), acmeedu.term_number(' 2023 / 2 ')), (2023, 2))
        self.assertEqual((acmeedu.term_year('2024'), acmeedu.term_number('2024/3')), (None, None))

    def test_attendance_pct(self):
        self.assertEqual(acmeedu.attendance_pct('85%'), Decimal('85.00'))
        self.assertEqual(acmeedu.attendance_pct('100%'), Decimal('100.00'))
        self.assertIsNone(acmeedu.attendance_pct(''))
        self.assertIsNone(acmeedu.attendance_pct('abc'))
        self.assertIsNone(acmeedu.attendance_pct(None))


class FakeSource:
    """Origem que devolve linhas fixas conforme a tabela da consulta."""

    def __init__(self, rows_by_table):
        self.rows_by_table = rows_by_table
        self.queries = []

    def limit_clause(self, limit):
        return limit or ''

    def scalar(self, sql):
        return len(self._rows(sql))

    def cursor(self):
        source = self

        class Cursor:
            def execute(self, sql):
                source.queries.append(sql)
                self.rows = source._rows(sql)

            def __iter__(self):
                return iter(self.rows)

        return Cursor()

    def _rows(self, sql):
        for table, rows in self.rows_by_table.items():
            if f'public.{table}' in sql.split('JOIN')[0]:
                return rows
        raise AssertionError(f'consulta inesperada: {sql}')


def make_migration(rows_by_table):
    written = []
    source = FakeSource(rows_by_table)
    context = types.SimpleNamespace(
        config={},
        artifacts=None,
        _reference_maps={},
        legacy_connections={},
        output=types.SimpleNamespace(write=written.append, comment=lambda text: None),
        logger=types.SimpleNamespace(print_log=lambda line: None),
        engine=lambda name: MigrationEngine(source, PostgresTargetAdapter(), written.append),
        load_reference_map=lambda key, sql: {'active': 1, 'on_hold': 2, 'graduated': 3, 'cancelled': 4},
        close=lambda: None,
    )
    migration = acmeedu.Migracao.__new__(acmeedu.Migracao)
    BaseMigration.__init__(migration, 'AcmeEdu', context=context)
    migration.print_log = lambda line: None
    migration.print_comment = lambda text: None
    return migration, written, source


STUDENT = {
    'cod_aluno': 300,
    'nome_aluno': 'Mariana  da Silva Santos',
    'dt_nasc': '02/10/1990',
    'cpf': '100.002.100-00',
    'fone': '51902375700',
    'email_contato': 'Email: Aluno300@AcmeEdu.Example / tel. recado',
    'sexo': 'F',
    'situacao': 'c',
    'cod_curso': 1,
    'nome_resp': 'JOÃO DA SILVA',
    'fone_resp': '+55 11 98765-4321',
    'parentesco': 'Pai',
    'obs': 'Documentos em \\\\servidor\\secretaria\\',
    'dt_cadastro': datetime.datetime(2016, 8, 22, 8, 0),
}


class GeneratedSqlTest(unittest.TestCase):
    def test_student_becomes_person_student_phone_and_guardian(self):
        no_extras = dict(STUDENT, cod_aluno=301, fone='', nome_resp='', dt_nasc='31/02/2001', cpf='', obs=None, sexo='')
        migration, written, _ = make_migration({'tb_aluno': [STUDENT, no_extras]})

        migration.insert_students()

        self.assertEqual(written, [
            "INSERT INTO public.people (id, name, cpf, birth_date, email, gender, created_at) VALUES "
            "(300, 'Mariana da Silva Santos', '10000210000', '1990-10-02', 'aluno300@acmeedu.example', 'F', "
            "'2016-08-22 08:00:00');\n",
            "INSERT INTO public.students (id, person_id, course_id, status_id, notes) VALUES "
            "(300, 300, 1, 4, E'Documentos em \\\\\\\\servidor\\\\secretaria\\\\');\n",
            "INSERT INTO public.phones (person_id, number, source) VALUES (300, '51902375700', 'student');\n",
            "INSERT INTO public.guardians (student_id, name, phone, relationship) VALUES "
            "(300, 'João da Silva', '11987654321', 'Pai');\n",
            "INSERT INTO public.people (id, name, cpf, birth_date, email, gender, created_at) VALUES "
            "(301, 'Mariana da Silva Santos', NULL, NULL, 'aluno300@acmeedu.example', NULL, "
            "'2016-08-22 08:00:00');\n",
            "INSERT INTO public.students (id, person_id, course_id, status_id, notes) VALUES "
            "(301, 301, 1, 4, NULL);\n",
        ])

    def test_teacher_who_is_student_reuses_person_and_skips_repeated_phone(self):
        shared = {
            'cod_prof': 6, 'nome_prof': 'Mariana da Silva Santos', 'cpf': '10000210000',
            'email': 'prof6@acmeedu.example', 'titulacao': '', 'fone1': '(51) 90237-5700',
            'fone2': '+55 71 97518-7514', 'person_id': 300, 'is_student': True, 'student_phone': '51902375700',
        }
        regular = {
            'cod_prof': 4, 'nome_prof': 'João de Fernandes Rodrigues', 'cpf': '500.000.044-00',
            'email': 'Prof4@AcmeEdu.Example', 'titulacao': 'mestrado', 'fone1': '(51) 99598-1676',
            'fone2': '51995981676', 'person_id': 100004, 'is_student': False, 'student_phone': None,
        }
        migration, written, source = make_migration({'tb_professor': [shared, regular]})

        migration.insert_teachers()

        self.assertIn('LEFT JOIN LATERAL', source.queries[0])
        self.assertIn('100000 + p.cod_prof', source.queries[0])
        self.assertEqual(written, [
            "INSERT INTO public.teachers (id, person_id, degree) VALUES (6, 300, NULL);\n",
            "INSERT INTO public.phones (person_id, number, source) VALUES (300, '71975187514', 'teacher');\n",
            "INSERT INTO public.people (id, name, cpf, email) VALUES "
            "(100004, 'João de Fernandes Rodrigues', '50000004400', 'prof4@acmeedu.example');\n",
            "INSERT INTO public.teachers (id, person_id, degree) VALUES (4, 100004, 2);\n",
            "INSERT INTO public.phones (person_id, number, source) VALUES (100004, '51995981676', 'teacher');\n",
        ])

    def test_courses_classes_and_enrollments(self):
        migration, written, _ = make_migration({
            'tb_curso': [{'cod_curso': 3, 'nome_curso': 'pedagogia ', 'sigla': 'PED', 'modalidade': 'p',
                          'carga_horaria': 3200, 'ativo': 'S'}],
            'tb_turma': [{'cod_turma': 7, 'cod_disc': 7, 'ano_semestre': '2022/1', 'sala': 'Sala 107'}],
            'tb_matricula': [{'cod_matricula': 97, 'cod_aluno': 25, 'cod_turma': 7, 'dt_matricula': '20230230',
                              'nota_final': Decimal('5.89'), 'frequencia': '85%', 'status': 'r'}],
        })

        migration.insert_courses()
        migration.insert_classes()
        migration.insert_enrollments()

        self.assertEqual(written, [
            "INSERT INTO public.courses (id, name, code, modality, workload_hours, active) VALUES "
            "(3, 'Pedagogia', 'PED', 1, 3200, true);\n",
            "INSERT INTO public.classes (id, subject_id, year, term, room) VALUES (7, 7, 2022, 1, 'Sala 107');\n",
            "INSERT INTO public.enrollments (id, student_id, class_id, enrolled_at, final_grade, attendance_pct, status) "
            "VALUES (97, 25, 7, NULL, '5.89', '85.00', 3);\n",
        ])


class PhaseSqlTest(unittest.TestCase):
    def test_dump_skips_every_legacy_table_and_sequence(self):
        migration, _, _ = make_migration({})

        ignored = migration.dump_ignoring_tables()

        for name in acmeedu.LEGACY_TABLES + acmeedu.LEGACY_SEQUENCES:
            self.assertIn(f'public.{name}', ignored)
        self.assertNotIn('public.holidays', ignored)
        self.assertNotIn('public.holidays_id_seq', ignored)

    def test_initial_sql_truncates_targets_and_disables_triggers(self):
        migration, written, _ = make_migration({})
        migration.disable_triggers = 'ALTER TABLE public.people DISABLE TRIGGER ALL;\n'

        migration.run_initial()

        self.assertTrue(written[0].startswith('TRUNCATE public.enrollments, public.classes'))
        self.assertIn('public.holidays RESTART IDENTITY;', written[0])
        self.assertNotIn('student_statuses', written[0])
        self.assertIn('ALTER TABLE public.people DISABLE TRIGGER ALL;\n', written)

    def test_sequences_use_id_column(self):
        migration, written, _ = make_migration({})
        migration.context.metadata = types.SimpleNamespace(
            reset_sequences_sql=lambda tables, column: f'-- {",".join(tables)} {column}\n'
        )

        migration.reset_sequences()

        self.assertEqual(written, [f'-- {",".join(acmeedu.TARGET_TABLES)} id\n'])


if __name__ == '__main__':
    unittest.main()
