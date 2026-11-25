from typing import Any, Iterator, Mapping, Protocol


class SourceAdapter(Protocol):
    """Contract for reading rows from a migration source.

    ``query`` is a ``SourceQuery`` or a ``NativeQuery`` from ``core.queries``.
    The adapter refuses a native query or field written for another adapter,
    and a query it cannot express, with ``UnsupportedQueryError`` before
    reading anything.
    """

    name: str
    """Adapter name, compared with the adapter declared by native queries and fields."""

    @classmethod
    def connect(cls, config) -> 'SourceAdapter':
        """Open the source from a ``ConnectionConfig``, importing the driver only here."""
        ...

    def read(self, query) -> Iterator[Mapping[str, Any]]:
        """Yield one mapping per row, keyed by field name."""
        ...

    def count(self, query) -> int | None:
        """Number of rows the query returns, for the progress, or None if unknown."""
        ...

    def close(self) -> None:
        """Close any resources owned by this adapter."""
        ...
