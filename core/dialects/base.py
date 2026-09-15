from typing import Any, Protocol


class Dialect(Protocol):
    """Contract for database-specific SQL rendering rules."""

    def serialize_value(self, value: Any) -> str:
        """Render a Python value as a SQL literal for this dialect."""
        ...

    def quote_identifier(self, identifier: str) -> str:
        """Render a database identifier for this dialect."""
        ...

    def limit_clause(self, limit: str) -> str:
        """Render an existing debug limit for this dialect."""
        ...
