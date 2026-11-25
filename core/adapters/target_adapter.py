from typing import Any, Iterator, Mapping, Protocol


class TargetAdapter(Protocol):
    """Contract for a migration target.

    The adapter renders the operations of ``core.operations`` into the
    commands of its artifact format and reads reference data with the queries
    of ``core.queries``. Applying the artifacts (session and capabilities)
    belongs to the application phase of the change and is not part of this
    contract yet.
    """

    name: str
    """Adapter name, compared with the adapter declared by ``Native`` operations."""

    artifact_format: Any
    """``ArtifactFormat`` of the files written for this target."""

    @classmethod
    def connect(cls, config) -> 'TargetAdapter':
        """Open the target from a ``ConnectionConfig``, importing the driver only here."""
        ...

    def render(self, operation) -> list[str]:
        """Commands of one operation, each ending in a line break. Empty when it means nothing here."""
        ...

    def read(self, query) -> Iterator[Mapping[str, Any]]:
        """Rows of a ``SourceQuery`` or ``NativeQuery`` read from the target."""
        ...

    def close_reads(self) -> None:
        """Read what rendering still needs, then refuse any later read of the target."""
        ...

    def close(self) -> None:
        """Close any resources owned by this adapter."""
        ...
