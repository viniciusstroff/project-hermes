import datetime
import unittest

import core.strategies as strategies
from core.adapters.postgres import PostgresTargetAdapter
from core.expand_migration import ExpandMigration
from core.migration_engine import MigrationEngine
from core.multi_target_migration import MultiTargetMigration, TargetMigration
from core.queries import NativeAdapterMismatchError, NativeField, SourceQuery
from core.sql import SqlLiteral
from core.strategies import (
    Copy,
    CpfClean,
    DateConvert,
    Default,
    EmailExtract,
    Fixed,
    Lookup,
    Lower,
    NullIfBlank,
    PhoneClean,
    RegexClean,
    Rename,
    Transform,
    Truncate,
    Upper,
)
from core.table_migration import TableMigration
from tests.source_fakes import FakeSqlSource

TARGET = PostgresTargetAdapter()


def render(strategy, row):
    return TARGET.serialize_value(strategy.raw_value(row))


def run_table(fields, source):
    writes = []
    TableMigration(
        source=SourceQuery('t'),
        target='public.t',
        fields=fields,
    ).run(MigrationEngine(source, TARGET, writes.append))
    return writes


class StrategiesWithoutSqlTest(unittest.TestCase):
    def test_module_has_no_fixed_dialect_serialization(self):
        self.assertFalse(hasattr(strategies, 'sql_value'))
        self.assertFalse(hasattr(strategies, '_POSTGRES_DIALECT'))
        self.assertFalse(hasattr(strategies, 'SqlExpression'))
        self.assertFalse(hasattr(strategies, 'SourceSqlExpression'))
        self.assertFalse(hasattr(strategies, 'check_source_dialect'))

    def test_core_strategies_have_no_value_or_render(self):
        examples = [Copy('a'), Rename('a', 'b'), Fixed('a', 1), Lookup('a', {}), Transform('a', lambda v, r: v),
                    RegexClean('a', 'x'), CpfClean('a'), PhoneClean('a'), DateConvert('a'), EmailExtract('a'),
                    NullIfBlank('a'), Truncate('a', 1), Upper('a'), Lower('a'), Default('a', 1),
                    NativeField('postgres', 'a', 'a')]
        for strategy in examples:
            with self.subTest(strategy=type(strategy).__name__):
                self.assertFalse(hasattr(strategy, 'value'))
                self.assertFalse(hasattr(strategy, 'render'))

    def test_cleaned_value_is_serialized_by_the_target_adapter(self):
        writes = run_table([CpfClean('f_cpf')], FakeSqlSource('postgres', [{'f_cpf': '123.456.789-00'}]))

        self.assertEqual(writes, ["INSERT INTO public.t (f_cpf) VALUES ('12345678900');\n"])

    def test_explicit_target_sql(self):
        writes = run_table(
            [Copy('f_id'), Fixed('f_created_at', SqlLiteral('CURRENT_TIMESTAMP'))],
            FakeSqlSource('postgres', [{'f_id': 1}]),
        )

        self.assertEqual(writes, ['INSERT INTO public.t (f_id, f_created_at) VALUES (1, CURRENT_TIMESTAMP);\n'])

    def test_field_without_raw_value_is_refused(self):
        class LegacyField:
            select_columns = ['f_id']
            insert_col = 'f_id'

            def value(self, row):
                return '1'

            def render(self, row, target_adapter):
                return '1'

        builders = [
            lambda field: TableMigration(SourceQuery('t'), 'public.t', [field]),
            lambda field: MultiTargetMigration(SourceQuery('t'), [TargetMigration('public.t', [field])]),
            lambda field: ExpandMigration(SourceQuery('t'), 'public.t', [Copy('f_id')], 'f_kind', [1],
                                          first_fields=[field]),
        ]
        for build in builders:
            with self.subTest(build=build):
                with self.assertRaises(TypeError) as raised:
                    build(LegacyField())
                self.assertIn('raw_value', str(raised.exception))


class LookupTest(unittest.TestCase):
    def test_uses_trimmed_key_and_fallback(self):
        strategy = Lookup('f_state', {'SP': 25}, fallback=99)
        self.assertEqual(strategy.raw_value({'f_state': ' SP '}), 25)
        self.assertEqual(strategy.raw_value({'f_state': 'RJ'}), 99)
        self.assertEqual(strategy.raw_value({'f_state': None}), 99)

    def test_supports_renamed_insert_col(self):
        strategy = Lookup('legacy', {'A': 1}, v2_name='modern', fallback=None)
        self.assertEqual(strategy.insert_col, 'modern')


class DateConvertTest(unittest.TestCase):
    def test_returns_date_rendered_by_target(self):
        strategy = DateConvert('f_birth')

        self.assertEqual(strategy.raw_value({'f_birth': '06/06/2026'}), datetime.date(2026, 6, 6))
        self.assertEqual(render(strategy, {'f_birth': '06/06/2026'}), "'2026-06-06'")

    def test_returns_datetime_when_asked(self):
        strategy = DateConvert('f_created', from_fmt='%d/%m/%Y %H:%M', as_datetime=True)

        self.assertEqual(strategy.raw_value({'f_created': '06/06/2026 10:30'}), datetime.datetime(2026, 6, 6, 10, 30))
        self.assertEqual(render(strategy, {'f_created': '06/06/2026 10:30'}), "'2026-06-06 10:30:00'")

    def test_returns_none_for_invalid_or_empty_values(self):
        strategy = DateConvert('f_birth')
        for value in ('31/02/2001', '', '   ', None, '99/99/9999'):
            with self.subTest(value=value):
                self.assertIsNone(strategy.raw_value({'f_birth': value}))
        self.assertEqual(render(strategy, {'f_birth': ''}), 'NULL')

    def test_renames_and_refuses_positional_target_format(self):
        self.assertEqual(DateConvert('f_date', v2_name='f_birthday').insert_col, 'f_birthday')
        with self.assertRaises(TypeError):
            DateConvert('f_created_at', '%Y%m%d', '%Y-%m-%d')


class EmailExtractTest(unittest.TestCase):
    def test_extracts_first_email_and_handles_missing(self):
        strategy = EmailExtract('f_email')
        self.assertEqual(strategy.raw_value({'f_email': 'foo bar teste@example.com outro@x.com'}), 'teste@example.com')
        self.assertIsNone(strategy.raw_value({'f_email': 'sem email aqui'}))
        self.assertIsNone(strategy.raw_value({'f_email': ''}))
        self.assertIsNone(strategy.raw_value({'f_email': None}))


class StrategyBasicsTest(unittest.TestCase):
    def test_rename_and_fixed(self):
        rename = Rename('legacy', 'modern')
        fixed = Fixed('f_create_user', 814)
        self.assertEqual((rename.select_columns, rename.insert_col), (['legacy'], 'modern'))
        self.assertEqual(rename.raw_value({'legacy': "O'Brien"}), "O'Brien")
        self.assertEqual(render(rename, {'legacy': "O'Brien"}), "'O''Brien'")
        self.assertEqual((fixed.select_columns, fixed.insert_col, fixed.raw_value({})), ([], 'f_create_user', 814))


class RegexCleanTest(unittest.TestCase):
    def test_keeps_backslash_when_pattern_keeps_it(self):
        strategy = RegexClean('f_path', r'\s+', ' ')

        self.assertEqual(strategy.raw_value({'f_path': 'C:\\docs  historico'}), 'C:\\docs historico')
        self.assertEqual(render(strategy, {'f_path': 'C:\\docs  historico'}), "E'C:\\\\docs historico'")

    def test_null_and_rename(self):
        strategy = RegexClean('f_doc_v1', r'[^\d]', v2_name='f_doc')

        self.assertEqual((strategy.select_columns, strategy.insert_col), (['f_doc_v1'], 'f_doc'))
        self.assertIsNone(strategy.raw_value({'f_doc_v1': None}))
        self.assertEqual(render(strategy, {'f_doc_v1': '123.456-78'}), "'12345678'")

    def test_cpf_and_phone_clean(self):
        cpf = CpfClean('f_cpf_v1', v2_name='f_cpf')
        phone = PhoneClean('f_mobile2', 'f_number1')

        self.assertEqual((cpf.insert_col, cpf.raw_value({'f_cpf_v1': '000.000.000-00'})), ('f_cpf', '00000000000'))
        self.assertEqual((phone.insert_col, phone.raw_value({'f_mobile2': '+55 (00) 9 0000-0000'})),
                         ('f_number1', '+5500900000000'))
        self.assertEqual(cpf.raw_value({'f_cpf_v1': ''}), '')


class PortableStrategiesTest(unittest.TestCase):
    def test_null_if_blank(self):
        strategy = NullIfBlank('f_cpf')
        for value in ('', '   ', None):
            with self.subTest(value=value):
                self.assertIsNone(strategy.raw_value({'f_cpf': value}))
        self.assertEqual(strategy.raw_value({'f_cpf': ' 123 '}), ' 123 ')
        self.assertEqual(strategy.raw_value({'f_cpf': 0}), 0)

    def test_truncate_counts_characters(self):
        strategy = Truncate('f_name', 3, v2_name='f_short')

        self.assertEqual(strategy.insert_col, 'f_short')
        self.assertEqual(strategy.raw_value({'f_name': 'Ãngela'}), 'Ãng')
        self.assertIsNone(strategy.raw_value({'f_name': None}))
        self.assertEqual(strategy.raw_value({'f_name': 12345}), 12345)
        with self.assertRaises(ValueError):
            Truncate('f_name', -1)

    def test_case_conversion(self):
        self.assertEqual(Upper('f_code').raw_value({'f_code': 'sp'}), 'SP')
        self.assertEqual(Upper('f_code').raw_value({'f_code': 'ação'}), 'AÇÃO')
        self.assertEqual(Lower('f_email').raw_value({'f_email': 'Ana@Exemplo.com'}), 'ana@exemplo.com')
        self.assertIsNone(Upper('f_code').raw_value({'f_code': None}))
        self.assertEqual(Lower('f_n').raw_value({'f_n': 7}), 7)

    def test_default_replaces_null(self):
        strategy = Default('f_type', 1)

        self.assertEqual(strategy.raw_value({'f_type': None}), 1)
        self.assertEqual(strategy.raw_value({'f_type': 3}), 3)
        self.assertEqual(strategy.raw_value({'f_type': ''}), '')

    def test_same_migration_on_postgres_and_firebird_sources(self):
        fields = [Copy('f_id'), NullIfBlank('f_cpf'), Truncate('f_name', 4), Upper('f_uf'), Default('f_type', 1)]
        row = {'f_id': 1, 'f_cpf': ' ', 'f_name': 'Mariana', 'f_uf': 'sp', 'f_type': None}
        postgres = FakeSqlSource('postgres', [row])
        firebird = FakeSqlSource('firebird', [row])

        postgres_writes = run_table(fields, postgres)
        firebird_writes = run_table(fields, firebird)

        self.assertEqual(postgres.queries[-1], 'SELECT f_id, f_cpf, f_name, f_uf, f_type FROM t')
        self.assertEqual(firebird.queries[-1], 'SELECT f_id, f_cpf, f_name, f_uf, f_type FROM t')
        self.assertEqual(postgres_writes, firebird_writes)
        self.assertEqual(
            postgres_writes,
            ["INSERT INTO public.t (f_id, f_cpf, f_name, f_uf, f_type) VALUES (1, NULL, 'Mari', 'SP', 1);\n"],
        )


class NativeFieldTest(unittest.TestCase):
    def test_matching_adapter_projects_the_expression(self):
        field = NativeField('postgres', "NULLIF(f_cpf, '')", 'f_cpf')
        source = FakeSqlSource('postgres', [{'f_cpf': None}])

        writes = run_table([field], source)

        self.assertEqual(source.queries[-1], "SELECT NULLIF(f_cpf, '') AS f_cpf FROM t")
        self.assertEqual(writes, ['INSERT INTO public.t (f_cpf) VALUES (NULL);\n'])

    def test_other_adapter_fails_before_any_query(self):
        builders = {
            'table': lambda field: TableMigration(SourceQuery('t'), 'public.t', [field]),
            'expand': lambda field: ExpandMigration(SourceQuery('t'), 'public.t', [Copy('f_id')], 'f_kind',
                                                    [1], first_fields=[field]),
            'multi': lambda field: MultiTargetMigration(SourceQuery('t'), [TargetMigration('public.t', [field])]),
        }
        for name, build in builders.items():
            with self.subTest(migration=name):
                source = FakeSqlSource('firebird', [{'f_cpf': '1'}])
                migration = build(NativeField('postgres', "NULLIF(f_cpf, '')", 'f_cpf'))

                with self.assertRaises(NativeAdapterMismatchError) as raised:
                    migration.run(MigrationEngine(source, TARGET, lambda sql: None))

                message = str(raised.exception)
                self.assertIn("'f_cpf'", message)
                self.assertIn("'postgres'", message)
                self.assertIn("'firebird'", message)
                self.assertIsInstance(raised.exception, ValueError)
                self.assertEqual(source.queries, [])

    def test_adapter_is_required(self):
        with self.assertRaises(TypeError):
            NativeField("NULLIF(f_cpf, '')", 'f_cpf')


if __name__ == '__main__':
    unittest.main()
