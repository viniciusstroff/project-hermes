"""Execution shared by the artifact applier and the direct database output.

Both run commands of a target session in a single transaction, with the same
two choices, each one a small object:

- the mode (``apply`` or ``dry-run``) decides how the transaction ends and
  the default error policy;
- the error policy (``stop`` or ``continue``) decides what happens when a
  command fails.

The applier reads the commands from artifact files after the generation; the
direct output reads them from the operations during the generation.
"""

from dataclasses import dataclass

from core.application import ArtifactReadError, CapabilityError, Command, DeferredCommandError, check_capabilities

APPLY = 'apply'
DRY_RUN = 'dry-run'

STOP = 'stop'
CONTINUE = 'continue'

COMMITTED = 'committed'
ROLLED_BACK = 'rolled_back'
FAILED = 'failed'

DEFAULT_BATCH_SIZE = 1000

RESULT_MESSAGES = {
    COMMITTED: 'COMMIT executado, carga gravada no destino',
    ROLLED_BACK: 'ROLLBACK executado, destino sem alterações',
    FAILED: 'ROLLBACK executado, nada da carga foi gravado',
}


class ApplicationError(Exception):
    """Expected reason for an application to fail.

    The caller rolls back, records the message in the report and ends with
    result ``failed``.
    """


class StatementFailedError(ApplicationError):
    """A command failed under the ``stop`` policy."""


class UnsupportedStatementError(ApplicationError):
    """A command the target session cannot run."""


class CommitFailedError(ApplicationError):
    """The final commit failed, for example on a deferred constraint."""


EXPECTED_FAILURES = (ApplicationError, ArtifactReadError, CapabilityError)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


class Mode:
    name = ''

    def default_on_error(self, capabilities) -> str:
        raise NotImplementedError

    def end_transaction(self, session) -> str:
        """End the transaction and return the report result."""

        raise NotImplementedError


class _ApplyMode(Mode):
    name = APPLY

    def default_on_error(self, capabilities) -> str:
        return STOP

    def end_transaction(self, session) -> str:
        try:
            session.commit()
        except Exception as exc:
            raise CommitFailedError(f'COMMIT falhou: {str(exc).strip()}') from exc
        return COMMITTED


class _DryRunMode(Mode):
    name = DRY_RUN

    def default_on_error(self, capabilities) -> str:
        # A dry run lists every error at once, when the target can undo one command.
        return CONTINUE if capabilities.savepoints else STOP

    def end_transaction(self, session) -> str:
        session.rollback()
        return ROLLED_BACK


MODES = {mode.name: mode for mode in (_ApplyMode(), _DryRunMode())}


def resolve_mode(mode: str, on_error: str, capabilities, adapter: str):
    """Mode object and error policy, refusing what the declared capabilities cannot honor."""

    if mode not in MODES:
        raise ValueError(f'modo inválido: {mode!r}. Use {APPLY} ou {DRY_RUN}.')
    mode_object = MODES[mode]
    on_error = on_error or mode_object.default_on_error(capabilities)
    if on_error not in POLICIES:
        raise ValueError(f'política de erro inválida: {on_error!r}. Use {STOP} ou {CONTINUE}.')
    check_capabilities(capabilities, adapter, mode, on_error)
    return mode_object, on_error


# ---------------------------------------------------------------------------
# Error policies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandItem:
    """A command with its place in the run, as the report shows it."""

    file: str
    number: int
    command: Command

    def location(self) -> str:
        return f'{self.file}, comando {self.number}, linha {self.command.line}'


class StopPolicy:
    """The first failing command ends the application.

    A session may defer commands to run them in batches (MongoDB). The policy
    keeps the deferred items, so a failure reported later still names the
    command, and counts a command as executed only when the session has run it.
    """

    def __init__(self, session, report, batch_size: int):
        self._session = session
        self._report = report
        self._pending = []

    def execute(self, item: CommandItem):
        self._pending.append(item)
        self._run(lambda: self._session.execute(item.command), item)

    def flush(self):
        self._run(self._session.flush, self._pending[0] if self._pending else None)

    def _run(self, call, current: CommandItem):
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

    def _fail(self, item: CommandItem, exc: BaseException):
        self._report.record_error(item.file, item.number, item.command, exc, self._session.error_code(exc))
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise StatementFailedError(f'{item.location()}: {message}') from exc


class ContinuePolicy:
    """Failing commands are rolled back and recorded, the others stay.

    Commands run inside a savepoint per batch. When one fails, the batch
    goes back to its savepoint and is replayed one command at a time, each
    with its own savepoint. The next command opens a new batch. It needs
    savepoints, so its sessions run each command when ``execute`` is called.
    """

    BATCH_SAVEPOINT = 'hermes_lote'
    STATEMENT_SAVEPOINT = 'hermes_comando'

    def __init__(self, session, report, batch_size: int):
        self._session = session
        self._report = report
        self._batch_size = batch_size
        self._batch = []

    def execute(self, item: CommandItem):
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


POLICIES = {STOP: StopPolicy, CONTINUE: ContinuePolicy}


def run_command(policy, report, item: CommandItem):
    """Count a skipped command, refuse an unsupported one or hand it to the policy."""

    if item.command.skip:
        report.record_skipped(item.file, item.command.skip)
        return
    if item.command.error:
        raise UnsupportedStatementError(f'{item.location()}: {item.command.error}')
    policy.execute(item)


def summarize_file(counters: dict) -> str:
    """Progress line with the counters of one artifact or phase."""

    skipped = sum(counters['skipped'].values())
    return (
        f"  {counters['file']}: executados {counters['executed']}, com erro {counters['errors']}, "
        f"pulados {skipped}, {counters['duration_seconds']:.1f} s"
    )


def rollback_quietly(session):
    try:
        session.rollback()
    except Exception:
        # A broken connection rolls back on the server when it closes.
        pass


def describe_unexpected(exc: BaseException) -> str:
    if isinstance(exc, KeyboardInterrupt):
        return 'aplicação interrompida'
    return f'{type(exc).__name__}: {str(exc).strip()}'
