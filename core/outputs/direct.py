"""Output that runs the operations on the target during the generation.

Each operation is rendered by the target adapter of the context, read into
commands by a session of the target adapter and handed to the same error
policy the artifact applier uses, before ``write()`` returns. A session that
runs commands in batches (MongoDB) runs a batch when it fills up, on a phase
change or at the end. The whole run is one transaction: ``finish(True)``
commits in ``apply`` mode and rolls back in ``dry-run`` mode.

The session has its own connection, opened only when ``prepare_target``
opens, after target reads closed. Operations of the ``setup`` phase wait in
memory until then, so nothing reaches the target while reference maps load.
"""

import io
import os
from typing import Callable

from core.application import CapabilityError, check_capabilities
from core.apply.execution import (
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
from core.apply.report import GENERATION, ApplicationReport
from core.artifacts import artifact_name

SETUP = 'setup'


class DirectDatabaseOutput:
    """Runs on the target, during the generation, the commands of each operation.

    ``target`` is the target adapter of the context: it renders operations
    and declares the capabilities checked here, before any connection.
    ``open_session`` is called without arguments when ``prepare_target``
    opens and returns a ``TargetSession`` on its own connection. ``log``
    receives progress messages and ``target_label`` names the target in them.
    """

    def __init__(
        self,
        target,
        open_session: Callable,
        run_dir: str,
        mode: str,
        on_error: str = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        log: Callable[[str], None] = None,
        target_label: str = None,
        report_clock=None,
    ):
        self.adapter = getattr(target, 'name', type(target).__name__)
        capabilities = getattr(target, 'capabilities', None)
        if capabilities is None or not callable(getattr(target, 'session', None)):
            raise CapabilityError(f"O adapter '{self.adapter}' não executa comandos no destino: falta session().")
        if batch_size < 1:
            raise ValueError('batch_size precisa ser maior que zero')

        self._mode, self.on_error = resolve_mode(mode, on_error, capabilities, self.adapter)
        self.mode = mode
        self.target = target
        self.run_dir = run_dir
        self.batch_size = batch_size
        self.report = None
        self._extension = target.artifact_format.extension
        self._open_session = open_session
        self._log = log or (lambda message: None)
        self._target_label = target_label
        self._report_options = {'clock': report_clock} if report_clock else {}
        self._dry_run = mode == DRY_RUN
        self._session = None
        self._policy = None
        self._held = []
        self._phase = None
        self._phase_open = False
        self._file = None
        self._line = 1
        self._number = 0
        self._failure = None
        self._finished = False
        self._closed = False

    # OutputAdapter --------------------------------------------------------

    def open_phase(self, phase: str):
        if self._session is None and phase == SETUP:
            # Held: target reads are still open while the context loads reference maps.
            self._phase = phase
            return
        if self._session is None:
            self._start()
        else:
            self._end_phase()
        self._start_phase(phase)

    def write(self, operation):
        if self._session is None:
            self._hold('operation', operation)
            return
        text = ''.join(self.target.render(operation))
        if text:
            self._run_lines(_lines(text))

    def include_file(self, path: str):
        if self._session is None:
            with open(path, encoding='utf-8', newline='') as lines:
                self._hold('lines', lines.readlines())
            return
        # Read line by line: a native copy may be several gigabytes. newline='' keeps \r\n in literals.
        with open(path, encoding='utf-8', newline='') as lines:
            self._run_lines(lines)

    def flush(self):
        if self._policy is not None:
            self._guard(self._policy.flush)

    def finish(self, success: bool):
        if self._finished:
            return
        self._finished = True
        if not success:
            self._abort(self._failure or 'a geração falhou antes do fim')
            return
        try:
            if self._session is None:
                self._start()
            self._end_phase()
            result = self._mode.end_transaction(self._session)
        except EXPECTED_FAILURES as exc:
            self._abort(str(exc))
            raise
        except BaseException as exc:
            self._abort(describe_unexpected(exc))
            raise
        self.report.finish(result)

    def describe(self) -> dict:
        report = os.path.relpath(self.report.directory, self.run_dir) if self.report is not None else None
        return {
            'type': 'direct',
            'adapter': self.adapter,
            'mode': self.mode,
            'on_error': self.on_error,
            'report': report,
        }

    def close(self):
        if self._closed:
            return
        self._closed = True
        if not self._finished:
            # Closed without finish(True), as after an interruption: nothing is confirmed.
            self._finished = True
            self._abort('execução não confirmada: o output foi fechado sem finish(True)')
        if self._session is not None:
            self._session.close()

    # Session and phases ---------------------------------------------------

    def _hold(self, kind: str, item):
        if self._phase != SETUP:
            raise RuntimeError('O output direto recebeu comandos antes de abrir uma fase.')
        self._held.append((kind, item))

    def _start(self):
        """Open the session after target reads closed, begin the transaction and run the held setup."""

        session = self._open_session()
        self._session = session
        self.report = ApplicationReport(
            self.run_dir, self.mode, self.on_error, session.describe(), source=GENERATION, **self._report_options,
        )
        self._log_start(session)
        # The session may know less than the declaration (a MongoDB server outside a replica set).
        self._guard(lambda: check_capabilities(session.capabilities, self.adapter, self.mode, self.on_error))
        session.begin()
        self._policy = POLICIES[self.on_error](session, self.report, self.batch_size)

        held, self._held = self._held, []
        if self._phase == SETUP:
            self._start_phase(SETUP)
            for kind, item in held:
                if kind == 'operation':
                    self.write(item)
                else:
                    self._run_lines(item)
            self._end_phase()

    def _start_phase(self, phase: str):
        self._phase = phase
        self._phase_open = True
        self._file = artifact_name(phase, self._extension)
        self._line = 1
        self._number = 0
        self.report.start_file(self._file)

    def _end_phase(self):
        """Run the deferred commands and undo the session settings of the phase."""

        if not self._phase_open:
            return
        self._phase_open = False
        self.flush()
        self._session.end_artifact()
        self.report.end_file(self._file)
        self._log(summarize_file(self.report.file_counters(self._file)))

    def _run_lines(self, lines):
        counted = _CountedLines(lines)

        def run():
            commands = self._session.commands(counted, self._dry_run, first_line=self._line, source=self._file)
            for command in commands:
                self._number += 1
                run_command(self._policy, self.report, CommandItem(self._file, self._number, command))

        self._guard(run)
        self._line += counted.count

    def _guard(self, call):
        """Keep the reason of an expected failure for the report, then let it go up."""

        try:
            return call()
        except EXPECTED_FAILURES as exc:
            self._failure = str(exc)
            raise

    def _abort(self, failure: str):
        if self._session is not None:
            rollback_quietly(self._session)
        if self.report is not None and self.report.result is None:
            self.report.finish(FAILED, failure)

    def _log_start(self, session):
        if self._target_label:
            self._log(f'Destino ({self.adapter}): {self._target_label}')
        self._log(f'Execução direta no destino. Modo: {self.mode}, política de erro: {self.on_error}')
        limit = session.describe().get('transaction_limit_seconds')
        if limit is not None:
            self._log(
                f'Limite de vida da transação do MongoDB: {limit} s. A geração inteira precisa caber nele.'
            )


def _lines(text: str) -> list:
    """Lines of rendered text, split as a file opened with ``newline=''`` would be.

    ``str.splitlines()`` also splits at characters such as ``\x85`` and
    ``\u2028``, which would change the line numbers and end a ``--`` comment
    early. A file splits only at ``\n``, ``\r\n`` and ``\r``.
    """

    return list(io.StringIO(text, newline=''))


class _CountedLines:
    """Iterates over lines and counts them, so the next text continues the numbering."""

    def __init__(self, lines):
        self._lines = lines
        self.count = 0

    def __iter__(self):
        for line in self._lines:
            self.count += 1
            yield line


def find_direct_output(output):
    """The DirectDatabaseOutput of an output, alone or inside a MirrorOutput, or None."""

    if isinstance(output, DirectDatabaseOutput):
        return output
    for inner in getattr(output, 'outputs', ()):
        found = find_direct_output(inner)
        if found is not None:
            return found
    return None
