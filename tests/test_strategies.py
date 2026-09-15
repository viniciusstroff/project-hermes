import unittest

from core.adapters import PostgresTargetAdapter
from core.strategies import DateConvert, EmailExtract, Fixed, Lookup, RegexClean, Rename, SqlExpression, sql_value


class SqlValueTest(unittest.TestCase):
    def test_handles_none_bool_number_and_strings(self):
        self.assertEqual(sql_value(None), 'NULL')
        self.assertEqual(sql_value(True), 'true')
        self.assertEqual(sql_value(False), 'false')
        self.assertEqual(sql_value(12), '12')
        self.assertEqual(sql_value(1.5), '1.5')
        self.assertEqual(sql_value("O'Brien"), "'O''Brien'")
        self.assertEqual(sql_value(r'a\b'), r"E'a\b'")

    def test_strategy_render_uses_target_adapter_without_breaking_value_compatibility(self):
        strategy = Rename('legacy', 'modern')
        target = PostgresTargetAdapter()

        self.assertEqual(strategy.raw_value({'legacy': "O'Brien"}), "O'Brien")
        self.assertEqual(strategy.value({'legacy': "O'Brien"}), "'O''Brien'")
        self.assertEqual(str(strategy.render({'legacy': "O'Brien"}, target)), "'O''Brien'")


class LookupTest(unittest.TestCase):
    def test_uses_trimmed_key_and_fallback(self):
        strategy = Lookup('f_state', {'SP': 25}, fallback=99)
        self.assertEqual(strategy.value({'f_state': ' SP '}), '25')
        self.assertEqual(strategy.value({'f_state': 'RJ'}), '99')
        self.assertEqual(strategy.value({'f_state': None}), '99')

    def test_supports_renamed_insert_col(self):
        strategy = Lookup('legacy', {'A': 1}, v2_name='modern', fallback=None)
        self.assertEqual(strategy.insert_col, 'modern')


class DateConvertTest(unittest.TestCase):
    def test_converts_valid_date(self):
        strategy = DateConvert('f_date')
        self.assertEqual(strategy.value({'f_date': '06/06/2026'}), "'2026-06-06'")

    def test_returns_null_for_invalid_or_empty_values(self):
        strategy = DateConvert('f_date')
        self.assertEqual(strategy.value({'f_date': ''}), 'NULL')
        self.assertEqual(strategy.value({'f_date': '99/99/9999'}), 'NULL')
        self.assertEqual(strategy.value({'f_date': None}), 'NULL')

    def test_supports_renamed_insert_col(self):
        strategy = DateConvert('f_date', v2_name='f_birthday')
        self.assertEqual(strategy.insert_col, 'f_birthday')


class EmailExtractTest(unittest.TestCase):
    def test_extracts_first_email_and_handles_missing(self):
        strategy = EmailExtract('f_email')
        self.assertEqual(strategy.value({'f_email': 'foo bar teste@example.com outro@x.com'}), "'teste@example.com'")
        self.assertEqual(strategy.value({'f_email': 'sem email aqui'}), 'NULL')
        self.assertEqual(strategy.value({'f_email': ''}), 'NULL')
        self.assertEqual(strategy.value({'f_email': None}), 'NULL')


class StrategyBasicsTest(unittest.TestCase):
    def test_rename_and_fixed(self):
        rename = Rename('legacy', 'modern')
        fixed = Fixed('f_create_user', 814)
        self.assertEqual(rename.select_columns, ['legacy'])
        self.assertEqual(rename.insert_col, 'modern')
        self.assertEqual(rename.value({'legacy': 'abc'}), "'abc'")
        self.assertEqual(fixed.select_columns, [])
        self.assertEqual(fixed.insert_col, 'f_create_user')
        self.assertEqual(fixed.value({}), '814')

    def test_regex_clean_and_sql_expression(self):
        regex = RegexClean('f_doc', r'[^\d]')
        expr = SqlExpression('COALESCE(f_type, 1)', 'f_type')
        self.assertEqual(regex.value({'f_doc': '123.456-78'}), "E'12345678'")
        self.assertEqual(regex.value({'f_doc': None}), 'NULL')
        self.assertEqual(expr.select_columns, ['COALESCE(f_type, 1) AS f_type'])
        self.assertEqual(expr.value({'f_type': 'abc'}), "'abc'")


if __name__ == '__main__':
    unittest.main()
