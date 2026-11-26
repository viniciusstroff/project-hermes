"""Split SQL artifacts into statements following PostgreSQL lexical rules.

Part of the PostgreSQL adapter: the session reads ``.sql`` artifacts with it.

The splitter reads its input line by line, so a dump of several gigabytes is
never loaded in memory. It only needs to know where each statement ends: a
semicolon outside string literals, quoted identifiers, dollar quotes and
comments. It does not validate the SQL. Malformed input is split the way the
``psql`` lexer would split it, so an ``E'...'`` string that ends in a
backslash keeps reading the following text.

Internally it is a state machine. ``_StatementSplitter`` holds the current
state and each state is a class that reads part of a line and decides the
next one:

- ``_Gap``: between statements, where blank text and comments are dropped;
- ``_InStatement``: inside a statement, looking for the semicolon;
- one ``_Literal`` subclass for each kind of text where a semicolon does
  not end the statement (strings, quoted identifiers, dollar quotes and
  block comments).
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Iterator

from core.application import ArtifactReadError

SQL = 'sql'
META_COMMAND = 'meta-command'


@dataclass(frozen=True)
class Statement:
    """One statement found in an artifact.

    ``kind`` is ``SQL`` for statements meant for the server and
    ``META_COMMAND`` for psql backslash commands. ``text`` has no leading
    comments, no trailing whitespace and no final semicolon. ``line`` is the
    1-based line where the statement starts.
    """

    kind: str
    text: str
    line: int


class UnterminatedLiteralError(ArtifactReadError):
    """Raised when the input ends inside a literal, identifier or comment."""

    def __init__(self, source: str, line: int, literal: str):
        self.source = source
        self.line = line
        self.literal = literal
        super().__init__(f'{source}: {literal} iniciado na linha {line} não foi fechado')


# Characters that may continue an identifier. A '$' or an E' right after
# one of them belongs to the identifier and does not open a literal.
_IDENT_CHAR = r'[\w$\x80-\U0010ffff]'
_TAG = r'(?:[A-Za-z_\x80-\U0010ffff][\w\x80-\U0010ffff]*)?'

# Group names other than semicolon and line_comment are keys of _LITERALS.
_STATEMENT_TOKENS = re.compile(
    rf"""
      (?P<semicolon>;)
    | (?P<escape_quote>(?<!{_IDENT_CHAR})[eE]')
    | (?P<quote>')
    | (?P<identifier>")
    | (?P<dollar>(?<!{_IDENT_CHAR})\${_TAG}\$)
    | (?P<line_comment>--)
    | (?P<comment>/\*)
    """,
    re.VERBOSE,
)
_ESCAPE_STRING_TOKENS = re.compile(r"[\\']")
_COMMENT_TOKENS = re.compile(r'/\*|\*/')
_WHITESPACE = re.compile(r'\s*')

_SCS_SETTING = re.compile(
    r"""
    \s*(?:
        SET\s+(?:SESSION\s+|LOCAL\s+)?standard_conforming_strings\s*(?:=|\bTO\b)\s*'?(?P<value>\w+)'?
      | RESET\s+(?P<reset>standard_conforming_strings|ALL)
    )\s*
    """,
    re.IGNORECASE | re.VERBOSE,
)
_TRUE_VALUES = {'on', 'true', 'yes', '1'}


def split_sql_file(path: str, standard_conforming_strings: bool = True) -> Iterator[Statement]:
    """Yield the statements of an SQL file encoded in UTF-8.

    The file is read without newline translation: lines end at ``\n``,
    ``\r\n`` or ``\r``, as in the server lexer, and a carriage return inside
    a literal stays in it.
    """

    with open(path, encoding='utf-8', newline='') as sql_file:
        yield from split_sql(sql_file, source=path, standard_conforming_strings=standard_conforming_strings)


def split_sql(
    lines: Iterable[str],
    source: str = '<sql>',
    standard_conforming_strings: bool = True,
    first_line: int = 1,
) -> Iterator[Statement]:
    """Yield the statements found in ``lines``.

    ``lines`` must keep their line endings, as file iteration does.
    ``standard_conforming_strings`` is the server setting at the start of the
    input. ``SET standard_conforming_strings`` statements found in the input
    update it, because with the setting off a backslash escapes the quote in
    a plain ``'...'`` string. ``first_line`` numbers the first line.
    """

    splitter = _StatementSplitter(source, standard_conforming_strings)
    for lineno, line in enumerate(lines, first_line):
        yield from splitter.feed(line, lineno)
    yield from splitter.finish()


class _State(ABC):
    """One lexical state of the splitter."""

    @abstractmethod
    def scan(self, splitter: '_StatementSplitter', line: str, pos: int, lineno: int) -> int:
        """Read ``line`` from ``pos`` and return where reading continues.

        The state may switch the splitter to another state before returning.
        """


class _Gap(_State):
    """Between statements: drops blank text and comments, reads meta-commands."""

    def scan(self, splitter, line, pos, lineno):
        pos = _WHITESPACE.match(line, pos).end()
        if pos == len(line) or line.startswith('--', pos):
            return len(line)
        if line[pos] == '\\':
            splitter.emit(Statement(META_COMMAND, line[pos:].rstrip('\r\n'), lineno))
            return len(line)
        if line.startswith('/*', pos):
            splitter.enter(_BlockComment(lineno))
            return pos + 2
        splitter.start_statement(lineno, pos)
        return pos


class _InStatement(_State):
    """Inside a statement: looks for the semicolon that ends it."""

    def scan(self, splitter, line, pos, lineno):
        match = _STATEMENT_TOKENS.search(line, pos)
        if match is None or match.lastgroup == 'line_comment':
            return len(line)
        if match.lastgroup == 'semicolon':
            splitter.end_statement(line, match.start())
        else:
            literal = _open_literal(match.lastgroup, match.group(), lineno, splitter.standard_conforming_strings)
            splitter.enter(literal)
        return match.end()


class _Literal(_State):
    """Text where a semicolon does not end the statement.

    ``line`` is where the literal was opened and ``description`` names it in
    the error raised when the input ends before it closes.
    """

    description = ''

    def __init__(self, lineno: int, opener: str = ''):
        self.line = lineno

    def scan(self, splitter, line, pos, lineno):
        end = self.find_end(line, pos)
        if end is None:
            return len(line)
        splitter.resume()
        return end

    @abstractmethod
    def find_end(self, line: str, pos: int) -> int | None:
        """Return the position right after the closing delimiter, or None
        when the literal goes on to the next line."""


class _QuotedText(_Literal):
    """Text closed by a delimiter that is written twice to appear inside it."""

    delimiter = ''

    def find_end(self, line, pos):
        while True:
            end = line.find(self.delimiter, pos)
            if end < 0:
                return None
            if not line.startswith(self.delimiter, end + 1):
                return end + 1
            pos = end + 2


class _StandardString(_QuotedText):
    delimiter = "'"
    description = "string '...'"


class _QuotedIdentifier(_QuotedText):
    delimiter = '"'
    description = 'identificador "..."'


class _EscapeString(_Literal):
    """``E'...'`` string, where a backslash escapes the next character."""

    description = "string E'...'"

    def __init__(self, lineno: int, opener: str = ''):
        super().__init__(lineno, opener)
        self._escapes_next_line = False

    def find_end(self, line, pos):
        if self._escapes_next_line:
            self._escapes_next_line = False
            pos += 1
        while True:
            match = _ESCAPE_STRING_TOKENS.search(line, pos)
            if match is None:
                return None
            pos = match.end()
            if match.group() == '\\':
                pos += 1
                if pos > len(line):
                    # Line without line ending: the escaped character opens the next one.
                    self._escapes_next_line = True
                    return None
            elif line.startswith("'", pos):
                pos += 1
            else:
                return pos


class _DollarQuote(_Literal):
    """``$tag$...$tag$`` string, closed only by the same tag."""

    def __init__(self, lineno: int, opener: str = ''):
        super().__init__(lineno, opener)
        self.tag = opener
        self.description = f'dollar quote {opener}'

    def find_end(self, line, pos):
        end = line.find(self.tag, pos)
        return None if end < 0 else end + len(self.tag)


class _BlockComment(_Literal):
    """``/* */`` comment, which PostgreSQL allows to be nested."""

    description = 'comentário /* */'

    def __init__(self, lineno: int, opener: str = '/*'):
        super().__init__(lineno, opener)
        self._depth = 1

    def find_end(self, line, pos):
        for match in _COMMENT_TOKENS.finditer(line, pos):
            self._depth += 1 if match.group() == '/*' else -1
            if self._depth == 0:
                return match.end()
        return None


_LITERALS = {
    'escape_quote': _EscapeString,
    'quote': _StandardString,
    'identifier': _QuotedIdentifier,
    'dollar': _DollarQuote,
    'comment': _BlockComment,
}

_GAP = _Gap()
_IN_STATEMENT = _InStatement()


def _open_literal(token: str, opener: str, lineno: int, standard_conforming_strings: bool) -> _Literal:
    literal_type = _LITERALS[token]
    if literal_type is _StandardString and not standard_conforming_strings:
        # With the setting off, a backslash also escapes the quote in '...'.
        literal_type = _EscapeString
    return literal_type(lineno, opener)


class _StatementBuffer:
    """Text of the statement being read, collected line by line."""

    def __init__(self):
        self.active = False
        self.start_line = 0
        self._chunks = []
        self._segment_start = 0

    def start(self, lineno: int, pos: int):
        self.active = True
        self.start_line = lineno
        self._segment_start = pos

    def end_line(self, line: str):
        if self.active:
            self._chunks.append(line[self._segment_start:])
        self._segment_start = 0

    def close(self, line: str = '', end: int = 0) -> str:
        """End the statement before ``line[end]`` and return its text."""

        self._chunks.append(line[self._segment_start:end])
        text = ''.join(self._chunks).rstrip()
        self._chunks = []
        self.active = False
        return text


class _StatementSplitter:
    """Context of the state machine.

    ``feed`` runs the current state over a line until the line is consumed
    and returns the statements completed in it. The states call ``enter``,
    ``resume``, ``emit``, ``start_statement`` and ``end_statement``.
    """

    def __init__(self, source: str, standard_conforming_strings: bool):
        self.standard_conforming_strings = standard_conforming_strings
        self._initial_standard_conforming_strings = standard_conforming_strings
        self._source = source
        self._state = _GAP
        self._statement = _StatementBuffer()
        self._ready = []

    def feed(self, line: str, lineno: int) -> list[Statement]:
        pos = 0
        size = len(line)
        while pos < size:
            pos = self._state.scan(self, line, pos, lineno)
        self._statement.end_line(line)
        return self._take_ready()

    def finish(self) -> list[Statement]:
        if isinstance(self._state, _Literal):
            raise UnterminatedLiteralError(self._source, self._state.line, self._state.description)
        if self._statement.active:
            self.end_statement()
        return self._take_ready()

    def enter(self, state: _State):
        self._state = state

    def resume(self):
        """Leave a literal and go back to the text around it."""

        self._state = _IN_STATEMENT if self._statement.active else _GAP

    def emit(self, statement: Statement):
        self._ready.append(statement)

    def start_statement(self, lineno: int, pos: int):
        self._statement.start(lineno, pos)
        self._state = _IN_STATEMENT

    def end_statement(self, line: str = '', end: int = 0):
        text = self._statement.close(line, end)
        self._state = _GAP
        if text:
            self.standard_conforming_strings = standard_conforming_strings_after(
                text,
                self.standard_conforming_strings,
                self._initial_standard_conforming_strings,
            )
            self.emit(Statement(SQL, text, self._statement.start_line))

    def _take_ready(self) -> list[Statement]:
        ready, self._ready = self._ready, []
        return ready


def standard_conforming_strings_after(text: str, current: bool, initial: bool) -> bool:
    """Value of standard_conforming_strings after ``text`` runs; ``initial`` is the value a RESET gives."""

    match = _SCS_SETTING.fullmatch(text)
    if match is None:
        return current
    value = (match.group('value') or 'default').lower()
    if match.group('reset') or value == 'default':
        return initial
    return value in _TRUE_VALUES
