from typing import Any, Iterator, Mapping, Protocol


class SourceCursor(Protocol):
    """Cursor shape consumed by migration classes."""

    def execute(self, sql: str) -> None:
        ...

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        ...


class SourceAdapter(Protocol):
    """Contract for reading rows from a migration source."""

    def cursor(self) -> SourceCursor:
        """Return a cursor that yields mapping-like rows."""
        ...

    def scalar(self, sql: str) -> Any:
        """Execute a query and return the first column from the first row."""
        ...

    def limit_clause(self, limit: str) -> str:
        """Render the debug limit syntax expected by this source."""
        ...

    def close(self) -> None:
        """Close any resources owned by this adapter."""
        ...
