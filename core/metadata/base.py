from typing import Any, Iterable, Protocol


class MetadataProvider(Protocol):
    """Contract for auxiliary target database metadata operations."""

    def load_reference_pairs(self, reference) -> Iterable[tuple[Any, Any]]:
        """Read the key and value pairs of a ``ReferenceMap`` declaration from the target."""
        ...

    def close_reads(self) -> None:
        """End target reads: later queries to the target must raise ``TargetReadClosedError``.

        Results already cached, such as trigger commands, stay available.
        """
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
