import datetime
import unittest
import uuid
from decimal import Decimal

from core.dialects import PostgresDialect, UnsupportedValueError


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



class PostgresTypeSerializationTest(unittest.TestCase):
    def setUp(self):
        self.render = PostgresDialect().serialize_value

    def test_scalars_keep_current_literals(self):
        self.assertEqual(self.render(None), 'NULL')
        self.assertEqual(self.render(True), 'true')
        self.assertEqual(self.render(False), 'false')
        self.assertEqual(self.render(12), '12')
        self.assertEqual(self.render(-3), '-3')
        self.assertEqual(self.render(1.5), '1.5')
        self.assertEqual(self.render("O'Brien"), "'O''Brien'")

    def test_binary_values_become_hex_decode(self):
        self.assertEqual(self.render(b'AB'), "decode('4142', 'hex')")
        self.assertEqual(self.render(bytearray(b'AB')), "decode('4142', 'hex')")
        rendered = self.render(memoryview(b'AB'))
        self.assertEqual(rendered, "decode('4142', 'hex')")
        self.assertNotIn('memory', rendered)
        self.assertEqual(self.render(b''), "decode('', 'hex')")
        self.assertEqual(self.render(b"\\'"), "decode('5c27', 'hex')")

    def test_special_floats(self):
        self.assertEqual(self.render(float('nan')), "'NaN'::float8")
        self.assertEqual(self.render(float('inf')), "'Infinity'::float8")
        self.assertEqual(self.render(float('-inf')), "'-Infinity'::float8")

    def test_decimals(self):
        self.assertEqual(self.render(Decimal('85.00')), '85.00')
        self.assertEqual(self.render(Decimal('-0.5')), '-0.5')
        self.assertEqual(self.render(Decimal('1E+2')), '1E+2')
        self.assertEqual(self.render(Decimal('NaN')), "'NaN'::numeric")
        self.assertEqual(self.render(Decimal('sNaN')), "'NaN'::numeric")
        self.assertEqual(self.render(Decimal('Infinity')), "'Infinity'::numeric")
        self.assertEqual(self.render(Decimal('-Infinity')), "'-Infinity'::numeric")

    def test_temporal_values_in_iso_format(self):
        minus_three = datetime.timezone(datetime.timedelta(hours=-3))
        self.assertEqual(self.render(datetime.date(2026, 6, 6)), "'2026-06-06'")
        self.assertEqual(self.render(datetime.datetime(2026, 6, 6, 10, 30)), "'2026-06-06 10:30:00'")
        self.assertEqual(
            self.render(datetime.datetime(2026, 6, 6, 10, 30, tzinfo=minus_three)),
            "'2026-06-06 10:30:00-03:00'",
        )
        self.assertEqual(self.render(datetime.datetime(2026, 6, 6, 10, 30, 0, 250)), "'2026-06-06 10:30:00.000250'")
        self.assertEqual(self.render(datetime.time(10, 30)), "'10:30:00'")

    def test_uuid_as_canonical_text(self):
        value = uuid.UUID('12345678-1234-5678-1234-567812345678')
        self.assertEqual(self.render(value), "'12345678-1234-5678-1234-567812345678'")

    def test_text_with_nul_is_refused_with_position(self):
        with self.assertRaises(UnsupportedValueError) as raised:
            self.render('abc\x00def')
        self.assertIn('position 3', str(raised.exception))
        self.assertNotIn('abc', str(raised.exception))

    def test_types_outside_the_list_are_refused(self):
        for value in (['a', 'b'], {'a': 1}, datetime.timedelta(days=1), object(), {1, 2}):
            with self.subTest(type=type(value).__name__):
                with self.assertRaises(UnsupportedValueError) as raised:
                    self.render(value)
                self.assertIn(type(value).__name__, str(raised.exception))
                self.assertIsInstance(raised.exception, TypeError)



class PostgresIdentifierTest(unittest.TestCase):
    def test_quotes_names_that_postgres_would_change_or_reject(self):
        quote = PostgresDialect().quote_identifier
        self.assertEqual(quote('public.states'), 'public.states')
        self.assertEqual(quote('f_code'), 'f_code')
        self.assertEqual(quote('Tipo'), '"Tipo"')
        self.assertEqual(quote('public.Estados'), 'public."Estados"')
        self.assertEqual(quote('nome com espaço'), '"nome com espaço"')
        self.assertEqual(quote('a"b'), '"a""b"')


if __name__ == '__main__':
    unittest.main()
