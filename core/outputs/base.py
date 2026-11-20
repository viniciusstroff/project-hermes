from typing import Protocol


class OutputAdapter(Protocol):
    """Contract for delivering generated migration commands.

    Phases are logical keys such as ``setup``, ``prepare_target``,
    ``load_transformed_data`` and ``finalize_target``. Each adapter decides how
    a phase maps to its own destination (a file, a database session, a stream).
    """

    def open_phase(self, phase: str) -> None:
        """Start delivering commands for the given logical phase."""
        ...

    def write(self, sql: str) -> None:
        """Deliver a chunk of generated SQL to the current phase."""
        ...

    def comment(self, text: str) -> None:
        """Deliver a human-readable section comment to the current phase."""
        ...

    def flush(self) -> None:
        """Push any buffered commands to the destination."""
        ...

    def close(self) -> None:
        """Flush pending commands and release resources owned by the output."""
        ...
