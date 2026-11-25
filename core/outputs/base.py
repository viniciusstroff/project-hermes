from typing import Protocol


class OutputAdapter(Protocol):
    """Contract for delivering the operations emitted by a migration.

    Phases are logical keys such as ``setup``, ``prepare_target``,
    ``load_transformed_data`` and ``finalize_target``. Each adapter decides how
    a phase maps to its own destination (a file, a database session, a stream).
    Operations come from ``core.operations``; the output asks the target
    adapter to render them.
    """

    def open_phase(self, phase: str) -> None:
        """Start delivering commands for the given logical phase."""
        ...

    def write(self, operation) -> None:
        """Deliver one operation to the current phase."""
        ...

    def flush(self) -> None:
        """Push any buffered commands to the destination."""
        ...

    def finish(self, success: bool) -> None:
        """Receive the run result once, after the last command of the run.

        ``True`` confirms the delivered commands; ``False`` means the run
        failed. An output closed without ``finish(True)`` must treat the run
        as unconfirmed (a database output rolls back, for instance).
        """
        ...

    def describe(self) -> dict:
        """Return a JSON-serializable description recorded in the manifest."""
        ...

    def close(self) -> None:
        """Flush pending commands and release resources owned by the output."""
        ...
