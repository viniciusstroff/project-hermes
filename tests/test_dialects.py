import unittest

from core.dialects import PostgresDialect


class PostgresStringSerializationTest(unittest.TestCase):
    def setUp(self):
        self.dialect = PostgresDialect()

    def test_string_without_backslash_stays_standard_literal(self):
        self.assertEqual(self.dialect.serialize_value('sem barra'), "'sem barra'")
        self.assertEqual(self.dialect.serialize_value("D'Ávila"), "'D''Ávila'")

    def test_backslash_is_doubled_inside_escape_string(self):
        self.assertEqual(self.dialect.serialize_value('a\\b'), "E'a\\\\b'")
        self.assertEqual(self.dialect.serialize_value('C:\\novo\\arquivo'), "E'C:\\\\novo\\\\arquivo'")

    def test_trailing_backslash_does_not_escape_closing_quote(self):
        self.assertEqual(self.dialect.serialize_value('fim\\'), "E'fim\\\\'")

    def test_backslash_next_to_quote(self):
        self.assertEqual(self.dialect.serialize_value("O\\'Neil"), "E'O\\\\''Neil'")

    def test_text_that_looks_like_escape_sequences(self):
        self.assertEqual(
            self.dialect.serialize_value('linha\\n\\x41\\101'),
            "E'linha\\\\n\\\\x41\\\\101'",
        )

    def test_real_line_break_is_kept_as_is(self):
        self.assertEqual(self.dialect.serialize_value('linha 1\nlinha 2'), "'linha 1\nlinha 2'")


if __name__ == '__main__':
    unittest.main()
