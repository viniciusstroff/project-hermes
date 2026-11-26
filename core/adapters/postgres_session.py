"""Application session of the PostgreSQL target adapter.

Everything the applier does that only makes sense in PostgreSQL lives here:
splitting ``.sql`` artifacts with the server lexical rules, the statements
that are counted instead of executed, ``RESET ALL`` between artifacts,
savepoints and the ``SQLSTATE`` of an error.

It is also where versions meet: a pg_dump newer than the target writes
settings the target does not have, such as ``transaction_timeout`` (17) on a
16 server. The session reads the parameters of the server once and skips a
``SET`` or ``set_config`` of a parameter the server does not know.
"""

import re

from core.adapters.postgres_splitter import META_COMMAND, split_sql_file
from core.application import Capabilities, Command

CAPABILITIES = Capabilities(transactions=True, savepoints=True, suspend_constraints=True)

_TRANSACTION_CONTROL = re.compile(r'(?:BEGIN|START\s+TRANSACTION|COMMIT|END|ROLLBACK|ABORT)\b', re.IGNORECASE)
_SEQUENCE_CHANGE = re.compile(r'SELECT\s+(?:pg_catalog\s*\.\s*)?setval\s*\(', re.IGNORECASE)
_COPY_FROM_STDIN = re.compile(r'COPY\b.*\bFROM\s+STDIN\b', re.IGNORECASE | re.DOTALL)
# SET [SESSION | LOCAL] name = value / TO value. SET TIME ZONE, SET ROLE and
# SET TRANSACTION do not match, because no "=" or TO follows the first word.
_SET_PARAMETER = re.compile(r'SET\s+(?:SESSION\s+|LOCAL\s+)?([A-Za-z_][\w.]*)\s*(?:=|TO\b)', re.IGNORECASE)
_SET_CONFIG = re.compile(r"SELECT\s+(?:pg_catalog\s*\.\s*)?set_config\s*\(\s*'([^']+)'", re.IGNORECASE)


class PostgresSession:
    """One psycopg2 connection used for one application, in a single transaction."""

    capabilities = CAPABILITIES
    # Every command runs when execute() is called.
    pending = 0

    def __init__(self, connection):
        self.connection = connection
        self._cursor = None
        self._standard_conforming_strings = None
        self._parameters = None

    def describe(self) -> dict:
        # get_dsn_parameters() already leaves the password out. Picking the
        # keys keeps it out even if a driver includes it.
        parameters = self.connection.get_dsn_parameters()
        return {
            'adapter': 'postgres',
            'host': parameters.get('host'),
            'port': parameters.get('port'),
            'database': parameters.get('dbname'),
            'user': parameters.get('user'),
        }

    def begin(self):
        # The generated files and pg_dump -E utf-8 are UTF-8.
        set_encoding = getattr(self.connection, 'set_client_encoding', None)
        if set_encoding is not None:
            set_encoding('UTF8')
        # psycopg2 opens the transaction on the first statement.
        self.connection.autocommit = False
        self._cursor = self.connection.cursor()
        self._cursor.execute('SHOW standard_conforming_strings')
        self._standard_conforming_strings = self._cursor.fetchone()[0] == 'on'
        self._cursor.execute('SHOW ALL')
        self._parameters = {row[0].lower() for row in self._cursor.fetchall()}

    def commands(self, path: str, dry_run: bool):
        for statement in split_sql_file(path, standard_conforming_strings=self._standard_conforming_strings):
            yield _command(statement, dry_run, self._parameters)

    def execute(self, command: Command):
        self._cursor.execute(command.text)

    def flush(self):
        pass

    def savepoint(self, name: str):
        self._cursor.execute(f'SAVEPOINT {name}')

    def rollback_to(self, name: str):
        self._cursor.execute(f'ROLLBACK TO SAVEPOINT {name}')

    def release(self, name: str):
        self._cursor.execute(f'RELEASE SAVEPOINT {name}')

    def end_artifact(self):
        # Each artifact used to run in its own psql session. Settings changed
        # by one (search_path from pg_dump) must not reach the next.
        self._cursor.execute('RESET ALL')

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def error_code(self, exc: BaseException):
        return getattr(exc, 'pgcode', None)

    def close(self):
        self.connection.close()


def _command(statement, dry_run: bool, parameters) -> Command:
    text = statement.text
    if statement.kind == META_COMMAND:
        return Command(text, statement.line, skip='meta_command')
    # A setting of a newer server version would fail the whole application.
    parameter = _set_parameter(text)
    if parameter is not None and parameters is not None and parameter.lower() not in parameters:
        return Command(text, statement.line, skip='unknown_setting')
    # A COMMIT from pg_dump around large objects would end the transaction early.
    if _TRANSACTION_CONTROL.match(text):
        return Command(text, statement.line, skip='transaction_control')
    # setval is not undone by ROLLBACK, so a dry run must not call it.
    if dry_run and _SEQUENCE_CHANGE.match(text):
        return Command(text, statement.line, skip='sequence_change')
    if _COPY_FROM_STDIN.match(text):
        return Command(
            text,
            statement.line,
            error='COPY ... FROM stdin não é suportado, porque os dados viriam no formato do psql. '
                  'Gere o dump com --column-inserts.',
        )
    return Command(text, statement.line)


def _set_parameter(text: str):
    """Parameter changed by a SET or set_config statement, or None for any other statement."""

    match = _SET_PARAMETER.match(text) or _SET_CONFIG.match(text)
    return match.group(1) if match else None
