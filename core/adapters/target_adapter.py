from typing import Any, Protocol, Sequence

from core.dialects import Dialect


class TargetAdapter(Protocol):
    """Contract for rendering operations against a migration target."""

    @property
    def dialect(self) -> Dialect:
        ...

    def serialize_value(self, value: Any) -> str:
        """Render a Python value as a target-compatible SQL literal."""
        ...

    def insert_statement(
        self,
        table: str,
        columns: Sequence[str],
        values: Sequence[Any],
    ) -> str:
        """Render an insert statement for the target."""
        ...
