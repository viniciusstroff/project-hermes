"""Apply the artifacts of a generated run in a single transaction.

The applier runs the artifacts listed in the manifest in phase order, in one
session of the target adapter and one transaction. It knows no database
language: the session reads each artifact into commands, marks the ones it
does not execute and runs the others. Two choices shape an application, each
one a small object:

- the mode (``apply`` or ``dry-run``) decides how the transaction ends and
  the default error policy;
- the error policy (``stop`` or ``continue``) decides what happens when a
  command fails.
"""

from dataclasses import dataclass
from typing import Callable

from core.adapters.registry import resolve_adapter
from core.application import ArtifactReadError, CapabilityError, Command, DeferredCommandError, check_capabilities
from core.apply.report import ApplicationReport
from core.apply.run import GeneratedRun, IncompleteRunError

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
    """A command failed under the ``stop`` policy."""


class UnsupportedStatementError(ApplicationError):
    """An artifact has a command the target session cannot run."""


class CommitFailedError(ApplicationError):
    """The final commit failed, for example on a deferred constraint."""


_EXPECTED_FAILURES = (ApplicationError, ArtifactReadError, CapabilityError)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


class _Mode:
    name = ''

    def default_on_error(self, capabilities) -> str:
        raise NotImplementedError

    def end_transaction(self, session) -> str:
        """End the transaction and return the report result."""

        raise NotImplementedError


class _ApplyMode(_Mode):
    name = APPLY

    def default_on_error(self, capabilities) -> str:
        return STOP

    def end_transaction(self, session) -> str:
        try:
            session.commit()
        except Exception as exc:
            raise CommitFailedError(f'COMMIT falhou: {str(exc).strip()}') from exc
        return COMMITTED


class _DryRunMode(_Mode):
    name = DRY_RUN

    def default_on_error(self, capabilities) -> str:
        # A dry run lists every error at once, when the target can undo one command.
        return CONTINUE if capabilities.savepoints else STOP

    def end_transaction(self, session) -> str:
        session.rollback()
        return ROLLED_BACK


_MODES = {mode.name: mode for mode in (_ApplyMode(), _DryRunMode())}


# ---------------------------------------------------------------------------
# Error policies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Item:
    """A command with its place in the run, as the report shows it."""

    file: str
    number: int
    command: Command

    def location(self) -> str:
        return f'{self.file}, comando {self.number}, linha {self.command.line}'


class _StopPolicy:
    """The first failing command ends the application.

    A session may defer commands to run them in batches (MongoDB). The policy
    keeps the deferred items, so a failure reported later still names the
    command of the artifact, and counts a command as executed only when the
    session has run it.
    """

    def __init__(self, session, report: ApplicationReport, batch_size: int):
        self._session = session
        self._report = report
        self._pending = []

    def execute(self, item: _Item):
        self._pending.append(item)
        self._run(lambda: self._session.execute(item.command), item)

    def flush(self):
        self._run(self._session.flush, self._pending[0] if self._pending else None)

    def _run(self, call, current: _Item):
        try:
            call()
        except DeferredCommandError as exc:
            self._fail(self._pending[exc.position], exc.cause)
        except Exception as exc:
            if current is None:
                raise
            self._fail(current, exc)
        # What the session no longer holds has run.
        still = len(self._pending) - getattr(self._session, 'pending', 0)
        for done in self._pending[:still]:
            self._report.record_executed(done.file)
        self._pending = self._pending[still:]

    def _fail(self, item: _Item, exc: BaseException):
        self._report.record_error(item.file, item.number, item.command, exc, self._session.error_code(exc))
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise StatementFailedError(f'{item.location()}: {message}') from exc


class _ContinuePolicy:
    """Failing commands are rolled back and recorded, the others stay.

    Commands run inside a savepoint per batch. When one fails, the batch
    goes back to its savepoint and is replayed one command at a time, each
    with its own savepoint. The next command opens a new batch. It needs
    savepoints, so its sessions run each command when ``execute`` is called.
    """

    BATCH_SAVEPOINT = 'hermes_lote'
    STATEMENT_SAVEPOINT = 'hermes_comando'

    def __init__(self, session, report: ApplicationReport, batch_size: int):
        self._session = session
        self._report = report
        self._batch_size = batch_size
        self._batch = []

    def execute(self, item: _Item):
        if not self._batch:
            self._session.savepoint(self.BATCH_SAVEPOINT)
        self._batch.append(item)

        try:
            self._session.execute(item.command)
        except Exception:
            self._session.rollback_to(self.BATCH_SAVEPOINT)
            self._replay_batch()
            return

        if len(self._batch) >= self._batch_size:
            self.flush()

    def flush(self):
        if not self._batch:
            return
        self._session.release(self.BATCH_SAVEPOINT)
        for item in self._batch:
            self._report.record_executed(item.file)
        self._batch = []

    def _replay_batch(self):
        batch, self._batch = self._batch, []
        for item in batch:
            self._session.savepoint(self.STATEMENT_SAVEPOINT)
            try:
                self._session.execute(item.command)
            except Exception as exc:
                self._session.rollback_to(self.STATEMENT_SAVEPOINT)
                self._report.record_error(item.file, item.number, item.command, exc, self._session.error_code(exc))
            else:
                self._report.record_executed(item.file)
            self._session.release(self.STATEMENT_SAVEPOINT)
        self._session.release(self.BATCH_SAVEPOINT)


_POLICIES = {STOP: _StopPolicy, CONTINUE: _ContinuePolicy}


# ---------------------------------------------------------------------------
# Applier
# ---------------------------------------------------------------------------


class ArtifactApplier:
    """Applies the artifacts of a generated run to the target, in one session.

    ``run`` is the run directory or a loaded ``GeneratedRun``. The target
    adapter comes from the manifest. Its declared capabilities pick the
    default error policy and refuse a mode or policy the target cannot
    honor, before any connection. ``open_session`` is called without
    arguments and returns a ``TargetSession`` of that adapter. It is called
    only after the run was checked, so an incomplete run never connects.

    ``log`` receives progress messages: the start and the counters of each
    artifact, and a line every ``progress_every`` commands read.
    """

    def __init__(
        self,
        run,
        open_session: Callable,
        mode: str = APPLY,
        on_error: str = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        report_clock=None,
        log: Callable[[str], None] = None,
        progress_every: int = DEFAULT_PROGRESS_EVERY,
    ):
        if mode not in _MODES:
            raise ValueError(f'modo inválido: {mode!r}. Use {APPLY} ou {DRY_RUN}.')
        if batch_size < 1 or progress_every < 1:
            raise ValueError('batch_size e progress_every precisam ser maiores que zero')

        self.run = run if isinstance(run, GeneratedRun) else GeneratedRun.load(run)
        self.adapter = resolve_adapter(self.run.target_adapter, 'target')
        self.capabilities = getattr(self.adapter, 'capabilities', None)
        if self.capabilities is None or not callable(getattr(self.adapter, 'session', None)):
            raise CapabilityError(f"O adapter '{self.run.target_adapter}' não aplica artefatos: falta session().")
        self._check_formats()

        self._mode = _MODES[mode]
        self.on_error = on_error or self._mode.default_on_error(self.capabilities)
        if self.on_error not in _POLICIES:
            raise ValueError(f'política de erro inválida: {self.on_error!r}. Use {STOP} ou {CONTINUE}.')
        check_capabilities(self.capabilities, self.run.target_adapter, mode, self.on_error)

        self.run_dir = self.run.run_dir
        self.mode = mode
        self.batch_size = batch_size
        self.progress_every = progress_every
        self._open_session = open_session
        self._report_options = {'clock': report_clock} if report_clock else {}
        self._log = log or (lambda message: None)

    def _check_formats(self):
        expected = self.adapter.artifact_format.name
        for artifact in self.run.artifacts:
            if artifact.format != expected:
                raise IncompleteRunError(
                    f"{self.run.run_dir}: {artifact.name} tem formato {artifact.format!r}, "
                    f"mas o adapter '{self.run.target_adapter}' lê {expected!r}."
                )

    def apply(self) -> ApplicationReport:
        """Apply the run and return its report.

        Expected failures (a command under ``stop``, an unsupported command,
        a failed commit, a capability the session lacks) end with result
        ``failed`` and no exception. Anything else, including an
        interruption, also rolls back and writes the report, then propagates.
        """

        session = self._open_session()
        try:
            return self._apply(session)
        finally:
            session.close()

    def _apply(self, session) -> ApplicationReport:
        report = ApplicationReport(self.run_dir, self.mode, self.on_error, session.describe(), **self._report_options)

        try:
            # The session may know less than the declaration (a MongoDB server outside a replica set).
            check_capabilities(session.capabilities, self.run.target_adapter, self.mode, self.on_error)
            session.begin()
            self._apply_artifacts(session, report)
            result = self._mode.end_transaction(session)
        except _EXPECTED_FAILURES as exc:
            _rollback_quietly(session)
            report.finish(FAILED, str(exc))
        except BaseException as exc:
            _rollback_quietly(session)
            report.finish(FAILED, _describe_unexpected(exc))
            raise
        else:
            report.finish(result)
        return report

    def _apply_artifacts(self, session, report: ApplicationReport):
        policy = _POLICIES[self.on_error](session, report, self.batch_size)
        dry_run = self.mode == DRY_RUN

        for artifact in self.run.artifacts:
            self._log(f'Aplicando {artifact.name}')
            report.start_file(artifact.name)
            for number, command in enumerate(session.commands(artifact.path, dry_run), 1):
                self._apply_command(policy, report, _Item(artifact.name, number, command))
                if number % self.progress_every == 0:
                    self._log(f'  {artifact.name}: {number} comandos lidos')
            policy.flush()
            session.end_artifact()
            report.end_file(artifact.name)
            self._log(_summarize_file(report.file_counters(artifact.name)))

    def _apply_command(self, policy, report: ApplicationReport, item: _Item):
        if item.command.skip:
            report.record_skipped(item.file, item.command.skip)
            return
        if item.command.error:
            raise UnsupportedStatementError(f'{item.location()}: {item.command.error}')
        policy.execute(item)


def _summarize_file(counters: dict) -> str:
    skipped = sum(counters['skipped'].values())
    return (
        f"  {counters['file']}: executados {counters['executed']}, com erro {counters['errors']}, "
        f"pulados {skipped}, {counters['duration_seconds']:.1f} s"
    )


def _rollback_quietly(session):
    try:
        session.rollback()
    except Exception:
        # A broken connection rolls back on the server when it closes.
        pass


def _describe_unexpected(exc: BaseException) -> str:
    if isinstance(exc, KeyboardInterrupt):
        return 'aplicação interrompida'
    return f'{type(exc).__name__}: {str(exc).strip()}'
