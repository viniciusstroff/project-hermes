"""Neutral contracts for applying generated artifacts to a target.

The applier in ``core/apply`` reads the run manifest, opens a session of the
target adapter and runs each command of each artifact through it. Everything
that depends on a database (how to split an artifact into commands, which
commands to skip, how a transaction and a savepoint work, what an error code
is) lives in the session of the adapter.
"""

from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Protocol


@dataclass(frozen=True)
class Capabilities:
    """What a target can honor during an application."""

    transactions: bool
    """Dry run and atomic application: everything or nothing."""

    savepoints: bool
    """The ``continue`` policy: undo only the failing commands."""

    suspend_constraints: bool
    """Turn off triggers and constraint checks while loading."""


@dataclass(frozen=True)
class Command:
    """One command read from an artifact.

    ``skip`` is the reason the session does not execute it, counted in the
    report. ``error`` is the reason the application cannot go on with it.
    ``line`` is the 1-based line where it starts in the artifact.
    """

    text: str
    line: int
    skip: str = None
    error: str = None
    payload: Any = None
    """Parsed form of the command, when the session reads it into something other than text."""


class ArtifactReadError(ValueError):
    """An artifact cannot be read into commands, such as an unterminated literal."""


class CapabilityError(Exception):
    """The target cannot honor the requested mode or error policy."""


class DeferredCommandError(Exception):
    """A command the session had deferred (to run it in a batch) failed.

    ``position`` counts the deferred commands from the oldest one still
    pending, in the order the session received them, so the applier can name
    the command of the artifact that failed.
    """

    def __init__(self, position: int, cause: BaseException):
        self.position = position
        self.cause = cause
        super().__init__(str(cause))


class TargetSession(Protocol):
    """One connection to the target, used for one application."""

    capabilities: Capabilities

    pending: int
    """Commands received by ``execute`` and not run yet. Zero for a session that never defers."""

    def describe(self) -> dict:
        """Adapter and connection of the session, without password, for the report."""
        ...

    def commands(self, lines: Iterable[str], dry_run: bool, first_line: int = 1, source: str = None) -> Iterator[Command]:
        """Read commands, in the format of the adapter, from lines that keep their line endings.

        The lines may be an open artifact file or text rendered in memory.
        ``first_line`` numbers the first line, so a text that starts in the
        middle of a phase keeps the line numbers of the phase file.
        ``source`` names the input in read errors.
        """
        ...

    def begin(self) -> None: ...

    def execute(self, command: Command) -> None:
        """Run a command, or defer it to a batch. A deferred failure raises ``DeferredCommandError``."""
        ...

    def flush(self) -> None:
        """Run every deferred command."""
        ...

    def savepoint(self, name: str) -> None: ...

    def rollback_to(self, name: str) -> None: ...

    def release(self, name: str) -> None: ...

    def end_artifact(self) -> None:
        """Undo session settings changed by an artifact before the next one."""
        ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def error_code(self, exc: BaseException) -> Any:
        """Code of a database error, as the adapter knows it, or None."""
        ...

    def close(self) -> None: ...


def required_capability(mode: str, on_error: str) -> list:
    """Capabilities a mode and an error policy need, with the reason, in check order."""

    needs = [('transactions', f'o modo {mode} precisa de transação')]
    if on_error == 'continue':
        needs.append(('savepoints', 'a política continue precisa de savepoint'))
    return needs


def check_capabilities(capabilities: Capabilities, adapter: str, mode: str, on_error: str):
    """Refuse a mode or policy the target cannot honor, naming the missing capability."""

    for name, reason in required_capability(mode, on_error):
        if not getattr(capabilities, name):
            raise CapabilityError(f"O destino '{adapter}' não suporta {_LABELS[name]}: {reason}.")


_LABELS = {'transactions': 'transações', 'savepoints': 'savepoints'}
