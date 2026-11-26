"""Apply the artifacts of a generated run in a single transaction.

The applier runs the artifacts listed in the manifest in phase order, in one
session of the target adapter and one transaction. It knows no database
language: the session reads each artifact into commands, marks the ones it
does not execute and runs the others. Modes and error policies come from
``core.apply.execution``, shared with the direct database output.
"""

from typing import Callable

from core.adapters.registry import resolve_adapter
from core.application import CapabilityError, check_capabilities
from core.apply.execution import (
    APPLY,
    DEFAULT_BATCH_SIZE,
    DRY_RUN,
    EXPECTED_FAILURES,
    FAILED,
    POLICIES,
    CommandItem,
    describe_unexpected,
    resolve_mode,
    rollback_quietly,
    run_command,
    summarize_file,
)
from core.apply.report import ApplicationReport
from core.apply.run import GeneratedRun, IncompleteRunError

DEFAULT_PROGRESS_EVERY = 50000


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
        if batch_size < 1 or progress_every < 1:
            raise ValueError('batch_size e progress_every precisam ser maiores que zero')

        self.run = run if isinstance(run, GeneratedRun) else GeneratedRun.load(run)
        self.adapter = resolve_adapter(self.run.target_adapter, 'target')
        self.capabilities = getattr(self.adapter, 'capabilities', None)
        if self.capabilities is None or not callable(getattr(self.adapter, 'session', None)):
            raise CapabilityError(f"O adapter '{self.run.target_adapter}' não aplica artefatos: falta session().")
        self._check_formats()

        self._mode, self.on_error = resolve_mode(mode, on_error, self.capabilities, self.run.target_adapter)

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
        except EXPECTED_FAILURES as exc:
            rollback_quietly(session)
            report.finish(FAILED, str(exc))
        except BaseException as exc:
            rollback_quietly(session)
            report.finish(FAILED, describe_unexpected(exc))
            raise
        else:
            report.finish(result)
        return report

    def _apply_artifacts(self, session, report: ApplicationReport):
        policy = POLICIES[self.on_error](session, report, self.batch_size)
        dry_run = self.mode == DRY_RUN

        for artifact in self.run.artifacts:
            self._log(f'Aplicando {artifact.name}')
            report.start_file(artifact.name)
            # newline='': a \r\n inside a literal must reach the target as it is.
            with open(artifact.path, encoding='utf-8', newline='') as lines:
                for number, command in enumerate(session.commands(lines, dry_run, source=artifact.name), 1):
                    run_command(policy, report, CommandItem(artifact.name, number, command))
                    if number % self.progress_every == 0:
                        self._log(f'  {artifact.name}: {number} comandos lidos')
            policy.flush()
            session.end_artifact()
            report.end_file(artifact.name)
            self._log(summarize_file(report.file_counters(artifact.name)))


