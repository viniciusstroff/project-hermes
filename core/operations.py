"""Write operations a migration emits, in no database language.

The migration describes what to do with the target. The target adapter
renders each operation into the commands of its artifact format: SQL for
PostgreSQL. An operation without meaning in a database renders into no
command. ``Native`` carries a command in the language of one adapter and is
refused by any other.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

from core.dialects.base import UnsupportedValueError


class ValueSerializationError(UnsupportedValueError):
    """A field value the target cannot represent, with the place it came from.

    The message names the target entity, the column and the Python type, and
    leaves the value out: it goes to the log and to the manifest.
    """

    def __init__(self, table: str, column: str, python_type: str, reason: str):
        self.table = table
        self.column = column
        self.python_type = python_type
        self.reason = reason
        super().__init__(f'{table}.{column} ({python_type}): {reason}')


def _entities(value) -> tuple:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


@dataclass(frozen=True)
class Insert:
    """One row (or document) with its values as Python objects."""

    entity: str
    values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, 'values', dict(self.values))


@dataclass(frozen=True)
class Clear:
    """Remove the data of the entities and keep their structure."""

    entities: tuple

    def __post_init__(self):
        entities = _entities(self.entities)
        if not entities:
            raise ValueError('Clear precisa de pelo menos uma entidade.')
        object.__setattr__(self, 'entities', entities)


@dataclass(frozen=True)
class SuspendConstraints:
    """Turn off triggers and constraint checks while loading. Empty: every entity."""

    entities: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, 'entities', _entities(self.entities))


@dataclass(frozen=True)
class ResumeConstraints:
    """Undo ``SuspendConstraints`` for the same entities. Empty: every entity."""

    entities: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, 'entities', _entities(self.entities))


@dataclass(frozen=True)
class ResetIdentity:
    """Move the identity generator of a column past the highest loaded value."""

    entity: str
    column: str = 'id'


@dataclass(frozen=True)
class Native:
    """A command in the language of one adapter, such as SQL text."""

    adapter: str
    payload: Any

    def __post_init__(self):
        if not isinstance(self.adapter, str) or not self.adapter:
            raise TypeError(f'Native precisa do nome do adapter; recebeu {self.adapter!r}.')


@dataclass(frozen=True)
class Comment:
    """A section title for whoever reads the artifact. Formats without comments drop it."""

    text: str
