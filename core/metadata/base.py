from typing import Any, Callable, Protocol


class MetadataProvider(Protocol):
    """Contract for auxiliary target database metadata operations."""

    def load_reference_map(
        self,
        sql: str,
        *,
        key_normalizer: Callable[[Any], Any] | None = None,
        value_normalizer: Callable[[Any], Any] | None = None,
        skip_null_keys: bool = True,
    ) -> dict[Any, Any]:
        """Load a reference map from the target database."""
        ...

    def disable_triggers_sql(self) -> str:
        """Return SQL that disables target triggers for migration writes."""
        ...

    def enable_triggers_sql(self) -> str:
        """Return SQL that enables target triggers after migration writes."""
        ...

    def disable_indexes_sql(self, indexes: list[str]) -> str:
        """Return SQL that disables the given target indexes."""
        ...

    def enable_indexes_sql(self, indexes: list[str]) -> str:
        """Return SQL that enables the given target indexes."""
        ...

    def reset_sequence_sql(self, table: str, column: str = 'f_id') -> str:
        """Return SQL that resets a target table sequence."""
        ...

    def reset_sequences_sql(self, tables: list[str], column: str = 'f_id') -> str:
        """Return SQL that resets several target table sequences."""
        ...
