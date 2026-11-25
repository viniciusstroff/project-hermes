"""Apply the SQL artifacts of a generated run in a single transaction.

The applier runs the artifacts in phase order on one connection and one
transaction. Two choices shape an application, each one a small object:

- the mode (``apply`` or ``dry-run``) decides how the transaction ends and
  which statements are skipped;
- the error policy (``stop`` or ``continue``) decides what happens when a
  statement fails.
"""

import re
from dataclasses import dataclass
from typing import Callable

from core.apply.report import ApplicationReport
from core.apply.run import GeneratedRun
from core.apply.splitter import META_COMMAND, Statement, UnterminatedLiteralError, split_sql_file

APPLY = 'apply'
DRY_RUN = 'dry-run'

STOP = 'stop'
CONTINUE = 'continue'

COMMITTED = 'committed'
ROLLED_BACK = 'rolled_back'
FAILED = 'failed'

DEFAULT_BATCH_SIZE = 1000
DEFAULT_PROGRESS_EVERY = 50000


class ApplicationError(Exception):
    """Expected reason for an application to fail.

    The applier rolls back, records the message in the report and returns
    the report with result ``failed`` instead of raising.
    """


class StatementFailedError(ApplicationError):
    """A statement failed under the ``stop`` policy."""


class UnsupportedStatementError(ApplicationError):
    """An artifact has a statement the applier cannot run."""


class CommitFailedError(ApplicationError):
    """The final COMMIT failed, for example on a deferred constraint."""


_EXPECTED_FAILURES = (ApplicationError, UnterminatedLiteralError)


# ---------------------------------------------------------------------------
# Statements the applier does not send to the server
# ---------------------------------------------------------------------------

_TRANSACTION_CONTROL = re.compile(r'(?:BEGIN|START\s+TRANSACTION|COMMIT|END|ROLLBACK|ABORT)\b', re.IGNORECASE)
_SEQUENCE_CHANGE = re.compile(r'SELECT\s+(?:pg_catalog\s*\.\s*)?setval\s*\(', re.IGNORECASE)
_COPY_FROM_STDIN = re.compile(r'COPY\b.*\bFROM\s+STDIN\b', re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class _SkipRule:
    """A kind of statement that is counted in the report instead of executed."""

    reason: str
    matches: Callable[[Statement], bool]


_META_COMMAND_RULE = _SkipRule('meta_command', lambda statement: statement.kind == META_COMMAND)
# A COMMIT from pg_dump around large objects would end the transaction early.
_TRANSACTION_CONTROL_RULE = _SkipRule(
    'transaction_control',
    lambda statement: _TRANSACTION_CONTROL.match(statement.text) is not None,
)
# setval is not undone by ROLLBACK, so a dry run must not call it.
_SEQUENCE_CHANGE_RULE = _SkipRule(
    'sequence_change',
    lambda statement: _SEQUENCE_CHANGE.match(statement.text) is not None,
)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


class _Mode:
    name = ''
    default_on_error = ''
    skip_rules = ()

    def skip_reason(self, statement: Statement):
        for rule in self.skip_rules:
            if rule.matches(statement):
                return rule.reason
        return None

    def end_transaction(self, connection) -> str:
        """End the transaction and return the report result."""

        raise NotImplementedError


class _ApplyMode(_Mode):
    name = APPLY
    default_on_error = STOP
    skip_rules = (_META_COMMAND_RULE, _TRANSACTION_CONTROL_RULE)

    def end_transaction(self, connection) -> str:
        try:
            connection.commit()
        except Exception as exc:
            raise CommitFailedError(f'COMMIT falhou: {str(exc).strip()}') from exc
        return COMMITTED


class _DryRunMode(_Mode):
    name = DRY_RUN
    # A dry run lists every error at once.
    default_on_error = CONTINUE
    skip_rules = (_META_COMMAND_RULE, _TRANSACTION_CONTROL_RULE, _SEQUENCE_CHANGE_RULE)

    def end_transaction(self, connection) -> str:
        connection.rollback()
        return ROLLED_BACK


_MODES = {mode.name: mode for mode in (_ApplyMode(), _DryRunMode())}


# ---------------------------------------------------------------------------
# Error policies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Item:
    """A statement with its place in the run, as the report shows it."""

    file: str
    number: int
    statement: Statement

    def location(self) -> str:
        return f'{self.file}, comando {self.number}, linha {self.statement.line}'


class _StopPolicy:
    """The first failing statement ends the application."""

    def __init__(self, cursor, report: ApplicationReport, batch_size: int):
        self._cursor = cursor
        self._report = report

    def execute(self, item: _Item):
        try:
            self._cursor.execute(item.statement.text)
        except Exception as exc:
            self._report.record_error(item.file, item.number, item.statement, exc)
            message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            raise StatementFailedError(f'{item.location()}: {message}') from exc
        self._report.record_executed(item.file)

    def flush(self):
        pass


class _ContinuePolicy:
    """Failing statements are rolled back and recorded, the others stay.

    Statements run inside a savepoint per batch. When one fails, the batch
    goes back to its savepoint and is replayed one statement at a time,
    each with its own savepoint. The next statement opens a new batch.
    """

    BATCH_SAVEPOINT = 'hermes_lote'
    STATEMENT_SAVEPOINT = 'hermes_comando'

    def __init__(self, cursor, report: ApplicationReport, batch_size: int):
        self._cursor = cursor
        self._report = report
        self._batch_size = batch_size
        self._batch = []

    def execute(self, item: _Item):
        if not self._batch:
            self._cursor.execute(f'SAVEPOINT {self.BATCH_SAVEPOINT}')
        self._batch.append(item)

        try:
            self._cursor.execute(item.statement.text)
        except Exception:
            self._cursor.execute(f'ROLLBACK TO SAVEPOINT {self.BATCH_SAVEPOINT}')
            self._replay_batch()
            return

        if len(self._batch) >= self._batch_size:
            self.flush()

    def flush(self):
        if not self._batch:
            return
        self._cursor.execute(f'RELEASE SAVEPOINT {self.BATCH_SAVEPOINT}')
        for item in self._batch:
            self._report.record_executed(item.file)
        self._batch = []

    def _replay_batch(self):
        batch, self._batch = self._batch, []
        for item in batch:
            self._cursor.execute(f'SAVEPOINT {self.STATEMENT_SAVEPOINT}')
            try:
                self._cursor.execute(item.statement.text)
            except Exception as exc:
                self._cursor.execute(f'ROLLBACK TO SAVEPOINT {self.STATEMENT_SAVEPOINT}')
                self._report.record_error(item.file, item.number, item.statement, exc)
            else:
                self._report.record_executed(item.file)
            self._cursor.execute(f'RELEASE SAVEPOINT {self.STATEMENT_SAVEPOINT}')
        self._cursor.execute(f'RELEASE SAVEPOINT {self.BATCH_SAVEPOINT}')


_POLICIES = {STOP: _StopPolicy, CONTINUE: _ContinuePolicy}


# ---------------------------------------------------------------------------
# Applier
# ---------------------------------------------------------------------------


class SqlArtifactApplier:
    """Applies the artifacts of ``run_dir`` to the target.

    ``connect`` is called without arguments and returns a DB-API connection
    (psycopg2 in production). It is called only after the run directory was
    checked, so an incomplete run never opens a connection.

    ``log`` receives progress messages: the start and the counters of each
    artifact, and a line every ``progress_every`` statements read.
    """

    def __init__(
        self,
        run_dir: str,
        connect: Callable,
        mode: str = APPLY,
        on_error: str = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        report_clock=None,
        log: Callable[[str], None] = None,
        progress_every: int = DEFAULT_PROGRESS_EVERY,
    ):
        if mode not in _MODES:
            raise ValueError(f'modo inválido: {mode!r}. Use {APPLY} ou {DRY_RUN}.')
        self._mode = _MODES[mode]
        self.on_error = on_error or self._mode.default_on_error
        if self.on_error not in _POLICIES:
            raise ValueError(f'política de erro inválida: {self.on_error!r}. Use {STOP} ou {CONTINUE}.')
        if batch_size < 1 or progress_every < 1:
            raise ValueError('batch_size e progress_every precisam ser maiores que zero')

        self.run_dir = run_dir
        self.mode = mode
        self.batch_size = batch_size
        self.progress_every = progress_every
        self._connect = connect
        self._report_options = {'clock': report_clock} if report_clock else {}
        self._log = log or (lambda message: None)

    def apply(self) -> ApplicationReport:
        """Apply the run and return its report.

        Expected failures (a statement under ``stop``, an unsupported
        statement, a failed COMMIT) end with result ``failed`` and no
        exception. Anything else, including an interruption, also rolls back
        and writes the report, then propagates.
        """

        run = GeneratedRun.load(self.run_dir)
        connection = self._connect()
        try:
            return self._apply(run, connection)
        finally:
            connection.close()

    def _apply(self, run: GeneratedRun, connection) -> ApplicationReport:
        connection.autocommit = False
        report = ApplicationReport(
            run.run_dir,
            self.mode,
            self.on_error,
            _describe_target(connection),
            **self._report_options,
        )

        try:
            self._apply_artifacts(run, connection, report)
            result = self._mode.end_transaction(connection)
        except _EXPECTED_FAILURES as exc:
            _rollback_quietly(connection)
            report.finish(FAILED, str(exc))
        except BaseException as exc:
            _rollback_quietly(connection)
            report.finish(FAILED, _describe_unexpected(exc))
            raise
        else:
            report.finish(result)
        return report

    def _apply_artifacts(self, run: GeneratedRun, connection, report: ApplicationReport):
        cursor = connection.cursor()
        standard_conforming_strings = _server_standard_conforming_strings(cursor)
        policy = _POLICIES[self.on_error](cursor, report, self.batch_size)

        for artifact in run.artifacts:
            self._log(f'Aplicando {artifact.name}')
            report.start_file(artifact.name)
            statements = split_sql_file(artifact.path, standard_conforming_strings=standard_conforming_strings)
            for number, statement in enumerate(statements, 1):
                self._apply_statement(policy, report, _Item(artifact.name, number, statement))
                if number % self.progress_every == 0:
                    self._log(f'  {artifact.name}: {number} comandos lidos')
            policy.flush()
            # Each artifact used to run in its own psql session. Settings
            # changed by one (search_path from pg_dump) must not reach the next.
            cursor.execute('RESET ALL')
            report.end_file(artifact.name)
            self._log(_summarize_file(report.file_counters(artifact.name)))

    def _apply_statement(self, policy, report: ApplicationReport, item: _Item):
        reason = self._mode.skip_reason(item.statement)
        if reason:
            report.record_skipped(item.file, reason)
            return
        if _COPY_FROM_STDIN.match(item.statement.text):
            raise UnsupportedStatementError(
                f'{item.location()}: COPY ... FROM stdin não é suportado, '
                'porque os dados viriam no formato do psql. Gere o dump com --column-inserts.'
            )
        policy.execute(item)


def _summarize_file(counters: dict) -> str:
    skipped = sum(counters['skipped'].values())
    return (
        f"  {counters['file']}: executados {counters['executed']}, com erro {counters['errors']}, "
        f"pulados {skipped}, {counters['duration_seconds']:.1f} s"
    )


def _server_standard_conforming_strings(cursor) -> bool:
    cursor.execute('SHOW standard_conforming_strings')
    return cursor.fetchone()[0] == 'on'


def _describe_target(connection) -> dict:
    # get_dsn_parameters() already leaves the password out. Picking the keys
    # keeps it out even if a driver includes it.
    parameters = connection.get_dsn_parameters()
    return {
        'host': parameters.get('host'),
        'port': parameters.get('port'),
        'database': parameters.get('dbname'),
        'user': parameters.get('user'),
    }


def _rollback_quietly(connection):
    try:
        connection.rollback()
    except Exception:
        # A broken connection rolls back on the server when it closes.
        pass


def _describe_unexpected(exc: BaseException) -> str:
    if isinstance(exc, KeyboardInterrupt):
        return 'aplicação interrompida'
    return f'{type(exc).__name__}: {str(exc).strip()}'
