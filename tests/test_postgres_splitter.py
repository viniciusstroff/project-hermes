import os
import sys
import tempfile
import types
import unittest
from io import StringIO

psycopg2 = types.ModuleType('psycopg2')
psycopg2_extras = types.ModuleType('psycopg2.extras')
psycopg2_extras.RealDictCursor = object
psycopg2.extras = psycopg2_extras
sys.modules.setdefault('psycopg2', psycopg2)
sys.modules.setdefault('psycopg2.extras', psycopg2_extras)

from core.adapters.postgres import PostgresTargetAdapter
from core.adapters.postgres_splitter import (
    META_COMMAND,
    SQL,
    Statement,
    UnterminatedLiteralError,
    split_sql,
    split_sql_file,
)
from core.operations import Comment, Insert, Native
from core.outputs import FileOutput
from core.sql import SqlLiteral


def insert(entity, row):
    return Insert(entity, dict(zip(('id', 'nome', 'valor', 'ativo'), row)))


def split(text, **kwargs):
    return list(split_sql(StringIO(text), **kwargs))


def texts(text, **kwargs):
    return [statement.text for statement in split(text, **kwargs)]


PG_DUMP_SAMPLE = """\
--
-- PostgreSQL database dump
--

\\restrict Hh3kQ9vXyZ

-- Dumped from database version 9.6.24
-- Dumped by pg_dump version 9.6.24

SET statement_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;

--
-- Data for Name: clientes; Type: TABLE DATA; Schema: public; Owner: postgres
--

ALTER TABLE public.clientes DISABLE TRIGGER ALL;

INSERT INTO public.clientes (id, nome, obs) VALUES (1, 'D''Ávila; Ltda', 'linha 1;
linha 2 -- sem comentário;
linha 3');

ALTER TABLE public.clientes ENABLE TRIGGER ALL;

--
-- Data for Name: BLOBS; Type: BLOBS; Schema: -; Owner: -
--

BEGIN;

SELECT pg_catalog.lo_open('16385', 131072);
SELECT pg_catalog.lowrite(0, '\\x48656c6c6f3b');
SELECT pg_catalog.lo_close(0);

COMMIT;

SELECT pg_catalog.setval('public.clientes_id_seq', 1, true);

--
-- PostgreSQL database dump complete
--

\\unrestrict Hh3kQ9vXyZ
"""


class PgDumpInputTest(unittest.TestCase):
    def test_splits_pg_dump_output(self):
        statements = split(PG_DUMP_SAMPLE)

        self.assertEqual(
            statements,
            [
                Statement(META_COMMAND, '\\restrict Hh3kQ9vXyZ', 5),
                Statement(SQL, 'SET statement_timeout = 0', 10),
                Statement(SQL, "SET client_encoding = 'UTF8'", 11),
                Statement(SQL, 'SET standard_conforming_strings = on', 12),
                Statement(SQL, "SELECT pg_catalog.set_config('search_path', '', false)", 13),
                Statement(SQL, 'SET check_function_bodies = false', 14),
                Statement(SQL, 'ALTER TABLE public.clientes DISABLE TRIGGER ALL', 20),
                Statement(
                    SQL,
                    "INSERT INTO public.clientes (id, nome, obs) VALUES (1, 'D''Ávila; Ltda', 'linha 1;\n"
                    'linha 2 -- sem comentário;\n'
                    "linha 3')",
                    22,
                ),
                Statement(SQL, 'ALTER TABLE public.clientes ENABLE TRIGGER ALL', 26),
                Statement(SQL, 'BEGIN', 32),
                Statement(SQL, "SELECT pg_catalog.lo_open('16385', 131072)", 34),
                Statement(SQL, "SELECT pg_catalog.lowrite(0, '\\x48656c6c6f3b')", 35),
                Statement(SQL, 'SELECT pg_catalog.lo_close(0)', 36),
                Statement(SQL, 'COMMIT', 38),
                Statement(SQL, "SELECT pg_catalog.setval('public.clientes_id_seq', 1, true)", 40),
                Statement(META_COMMAND, '\\unrestrict Hh3kQ9vXyZ', 46),
            ],
        )

    def test_reads_file_from_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, '02_dump_compatible_tables.sql')
            with open(path, 'w', encoding='utf-8') as sql_file:
                sql_file.write(PG_DUMP_SAMPLE)

            self.assertEqual(list(split_sql_file(path)), split(PG_DUMP_SAMPLE))


class LiteralTest(unittest.TestCase):
    def test_semicolons_inside_literals(self):
        sql = "INSERT INTO t VALUES ('a;b', E'c\\';d', $x$e;f$x$);\nSELECT 2;\n"

        self.assertEqual(
            texts(sql),
            ["INSERT INTO t VALUES ('a;b', E'c\\';d', $x$e;f$x$)", 'SELECT 2'],
        )

    def test_quoted_identifiers(self):
        self.assertEqual(
            texts('SELECT "a;b", "x""y;" FROM t; SELECT 2;'),
            ['SELECT "a;b", "x""y;" FROM t', 'SELECT 2'],
        )

    def test_escape_string_with_doubled_quote_and_escaped_backslash(self):
        self.assertEqual(
            texts("SELECT E'a''b;\\\\'; SELECT 2;"),
            ["SELECT E'a''b;\\\\'", 'SELECT 2'],
        )

    def test_lowercase_escape_string_prefix(self):
        self.assertEqual(texts("SELECT e'\\';'; SELECT 2;"), ["SELECT e'\\';'", 'SELECT 2'])

    def test_quote_after_identifier_ending_in_e_is_a_plain_string(self):
        # "namE" is one identifier, so the string after it follows the
        # standard rules and the backslash does not escape the quote.
        self.assertEqual(
            texts("SELECT 1 AS namE'\\'; SELECT 2;"),
            ["SELECT 1 AS namE'\\'", 'SELECT 2'],
        )

    def test_multiline_dollar_quote_with_tag(self):
        sql = (
            'CREATE FUNCTION f(int) RETURNS int AS $body$\n'
            'BEGIN\n'
            '  RETURN $1; -- $x$ não fecha nada\n'
            'END;\n'
            '$body$ LANGUAGE plpgsql;\n'
            'SELECT f(1);\n'
        )

        statements = split(sql)

        self.assertEqual([statement.line for statement in statements], [1, 6])
        self.assertTrue(statements[0].text.endswith('$body$ LANGUAGE plpgsql'))
        self.assertEqual(statements[1].text, 'SELECT f(1)')

    def test_anonymous_dollar_quote(self):
        self.assertEqual(
            texts('DO $$ BEGIN PERFORM 1; END $$; SELECT 2;'),
            ['DO $$ BEGIN PERFORM 1; END $$', 'SELECT 2'],
        )

    def test_dollar_inside_identifier_does_not_open_quote(self):
        self.assertEqual(texts('SELECT a$b$c FROM t; SELECT 2;'), ['SELECT a$b$c FROM t', 'SELECT 2'])

    def test_positional_parameter_does_not_open_quote(self):
        self.assertEqual(texts('SELECT $1; SELECT 2;'), ['SELECT $1', 'SELECT 2'])

    def test_escape_string_ending_in_backslash_swallows_following_text(self):
        # Same behavior as psql: the backslash escapes the closing quote.
        self.assertEqual(
            texts("SELECT E'a\\'; SELECT ''';\nSELECT 2;\n"),
            ["SELECT E'a\\'; SELECT '''", 'SELECT 2'],
        )

    def test_backslash_escapes_newline_inside_escape_string(self):
        self.assertEqual(
            texts("SELECT E'a\\\n;b'; SELECT 2;"),
            ["SELECT E'a\\\n;b'", 'SELECT 2'],
        )


class StandardConformingStringsTest(unittest.TestCase):
    SQL = "INSERT INTO t VALUES ('a\\';b');\nSELECT 2;\n"

    def test_backslash_is_literal_by_default(self):
        with self.assertRaises(UnterminatedLiteralError):
            split(self.SQL)

    def test_backslash_escapes_quote_when_setting_is_off(self):
        self.assertEqual(
            texts(self.SQL, standard_conforming_strings=False),
            ["INSERT INTO t VALUES ('a\\';b')", 'SELECT 2'],
        )

    def test_follows_set_statements_in_the_input(self):
        sql = (
            'SET standard_conforming_strings = off;\n'
            "SELECT 'a\\';b';\n"
            "SET standard_conforming_strings TO 'on';\n"
            "SELECT 'c\\';\n"
        )

        self.assertEqual(
            texts(sql),
            [
                'SET standard_conforming_strings = off',
                "SELECT 'a\\';b'",
                "SET standard_conforming_strings TO 'on'",
                "SELECT 'c\\'",
            ],
        )

    def test_reset_returns_to_initial_setting(self):
        sql = (
            'SET standard_conforming_strings = off;\n'
            'RESET ALL;\n'
            "SELECT 'c\\';\n"
        )

        self.assertEqual(texts(sql)[-1], "SELECT 'c\\'")


class CommentTest(unittest.TestCase):
    def test_leading_comments_are_dropped_and_line_points_to_statement(self):
        sql = '-- cabeçalho;\n/* bloco;\n   continua */\n\n  SELECT 1;\n'

        self.assertEqual(split(sql), [Statement(SQL, 'SELECT 1', 5)])

    def test_nested_block_comments(self):
        self.assertEqual(
            texts('SELECT /* a; /* b; */ c; */ 1; SELECT 2;'),
            ['SELECT /* a; /* b; */ c; */ 1', 'SELECT 2'],
        )

    def test_line_comment_inside_statement_is_kept(self):
        self.assertEqual(
            texts('SELECT 1 -- a;b\n, 2;\n'),
            ['SELECT 1 -- a;b\n, 2'],
        )

    def test_quotes_inside_comments_are_ignored(self):
        self.assertEqual(
            texts("-- it's\nSELECT /* \" $$ */ 1; SELECT 2;"),
            ['SELECT /* " $$ */ 1', 'SELECT 2'],
        )

    def test_comment_only_input_yields_nothing(self):
        self.assertEqual(split('-- nada\n/* aqui */\n\n'), [])


class StatementBoundaryTest(unittest.TestCase):
    def test_statement_without_final_semicolon(self):
        self.assertEqual(
            split('SELECT 1;\nSELECT 2\n  -- fim\n'),
            [Statement(SQL, 'SELECT 1', 1), Statement(SQL, 'SELECT 2\n  -- fim', 2)],
        )

    def test_empty_statements_are_skipped(self):
        self.assertEqual(texts(';;\nSELECT 1;; ;'), ['SELECT 1'])

    def test_several_statements_on_one_line(self):
        self.assertEqual(
            split("SELECT 1; SELECT ';'; SELECT 3;"),
            [Statement(SQL, 'SELECT 1', 1), Statement(SQL, "SELECT ';'", 1), Statement(SQL, 'SELECT 3', 1)],
        )

    def test_meta_command_after_statement_on_same_line(self):
        self.assertEqual(
            split('SELECT 1; \\echo oi\n'),
            [Statement(SQL, 'SELECT 1', 1), Statement(META_COMMAND, '\\echo oi', 1)],
        )

    def test_backslash_inside_statement_is_not_a_meta_command(self):
        self.assertEqual(texts('SELECT 1\n\\x\n;'), ['SELECT 1\n\\x'])

    def test_crlf_line_endings(self):
        self.assertEqual(
            split('SELECT 1;\r\n\\restrict k\r\nSELECT 2;\r\n'),
            [Statement(SQL, 'SELECT 1', 1), Statement(META_COMMAND, '\\restrict k', 2), Statement(SQL, 'SELECT 2', 3)],
        )


class UnterminatedTest(unittest.TestCase):
    def assert_unterminated(self, sql, line, literal):
        with self.assertRaises(UnterminatedLiteralError) as raised:
            split(sql, source='03_load_transformed_data.sql')

        self.assertEqual(raised.exception.source, '03_load_transformed_data.sql')
        self.assertEqual(raised.exception.line, line)
        self.assertEqual(raised.exception.literal, literal)
        self.assertIn('03_load_transformed_data.sql', str(raised.exception))
        self.assertIn(f'linha {line}', str(raised.exception))

    def test_unterminated_string(self):
        self.assert_unterminated("SELECT 1;\nINSERT INTO t VALUES ('abc);\nSELECT 2;\n", 2, "string '...'")

    def test_unterminated_escape_string(self):
        self.assert_unterminated("SELECT E'abc\\';\n", 1, "string E'...'")

    def test_unterminated_identifier(self):
        self.assert_unterminated('SELECT 1;\n\nSELECT "abc;\n', 3, 'identificador "..."')

    def test_unterminated_dollar_quote(self):
        self.assert_unterminated('DO $body$ BEGIN END; $$;\n', 1, 'dollar quote $body$')

    def test_unterminated_block_comment(self):
        self.assert_unterminated('SELECT 1; /* a /* b */\nSELECT 2;\n', 1, 'comentário /* */')


class CoreGeneratedSqlTest(unittest.TestCase):
    class FakeArtifacts:
        def __init__(self, directory):
            self.directory = directory

        def command_path(self, phase, extension):
            return os.path.join(self.directory, f'{phase}.{extension}')

    def test_splits_sql_written_by_core_outputs(self):
        adapter = PostgresTargetAdapter()
        rows = [
            (1, "O'Neil; Ltda", None, True),
            (2, 'linha 1\nlinha 2; -- não é comentário', 3.5, False),
            (3, 'C:\\pasta\\arquivo;', SqlLiteral('NOW()'), None),
            (4, '/* não é comentário */ $$ "', 0, True),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output = FileOutput(self.FakeArtifacts(tmpdir), adapter, buffer_size=2)
            output.open_phase('load_transformed_data')
            output.write(Comment('Tabela clientes; com ponto e vírgula'))
            for row in rows:
                output.write(insert('public.clientes', row))
            output.write(Native('postgres', "UPDATE public.clientes SET nome = 'x;y' WHERE id = 1;\n"))
            output.close()

            statements = list(split_sql_file(os.path.join(tmpdir, 'load_transformed_data.sql')))

        expected = [adapter.render(insert('public.clientes', row))[0].rstrip().rstrip(';') for row in rows]
        expected.append("UPDATE public.clientes SET nome = 'x;y' WHERE id = 1")
        self.assertEqual([statement.kind for statement in statements], [SQL] * 5)
        self.assertEqual([statement.text for statement in statements], expected)
        self.assertEqual(statements[0].line, 6)

    def test_value_ending_in_backslash_does_not_absorb_next_statement(self):
        adapter = PostgresTargetAdapter()
        sql = (
            adapter.render(Insert('public.arquivos', {'id': 1, 'caminho': 'C:\\pasta\\'}))[0]
            + adapter.render(Insert('public.arquivos', {'id': 2, 'caminho': 'D:\\'}))[0]
        )

        statements = split(sql)

        self.assertEqual(
            [statement.text for statement in statements],
            [
                "INSERT INTO public.arquivos (id, caminho) VALUES (1, E'C:\\\\pasta\\\\')",
                "INSERT INTO public.arquivos (id, caminho) VALUES (2, E'D:\\\\')",
            ],
        )
        self.assertEqual([statement.line for statement in statements], [1, 2])


if __name__ == '__main__':
    unittest.main()
