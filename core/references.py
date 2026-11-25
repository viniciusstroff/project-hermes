"""Reference maps read from the migration target.

A migration declares its maps in ``declare_reference_maps()``. The runner
loads every declared map before the first phase opens and then closes target
reads, so the generation never reads the target in the middle of the load.
Migration code reads a loaded map with ``self.reference_map(name)``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


class ReferenceMapError(Exception):
    """A reference map declaration, load or lookup is invalid."""


class TargetReadClosedError(RuntimeError):
    """The target was queried after the reference maps were loaded."""


def _strip_key(value):
    return str(value).strip()


def _same_value(value):
    return value


@dataclass(frozen=True)
class ReferenceMap:
    """Declaration of a key to value map read from the target.

    Declare either ``table`` with ``key`` and ``value`` (and optional equality
    ``filters``), or ``query``: a target SQL statement whose first column is the
    key and second column is the value. ``query`` is dialect-dependent.
    """

    name: str
    table: str = None
    key: str = None
    value: str = None
    filters: Mapping[str, Any] = field(default_factory=dict)
    query: str = None
    key_normalizer: Callable[[Any], Any] = None
    value_normalizer: Callable[[Any], Any] = None
    skip_null_keys: bool = True

    def __post_init__(self):
        if not self.name:
            raise ReferenceMapError('Reference map without name')
        if (self.table is None) == (self.query is None):
            raise ReferenceMapError(f"Reference map '{self.name}': declare exactly one of table or query")
        if self.table is not None and not (self.key and self.value):
            raise ReferenceMapError(f"Reference map '{self.name}': a table declaration needs key and value")
        if self.query is not None and (self.key or self.value or self.filters):
            raise ReferenceMapError(
                f"Reference map '{self.name}': key, value and filters only apply to table declarations"
            )

    def normalize_key(self, raw):
        return (self.key_normalizer or _strip_key)(raw)

    def normalize_value(self, raw):
        return (self.value_normalizer or _same_value)(raw)


class LoadedReferenceMap(Mapping):
    """Read-only map loaded from a declaration, keeping its key normalizer."""

    def __init__(self, name: str, data: dict, key_normalizer: Callable[[Any], Any]):
        self.name = name
        self._data = dict(data)
        self._key_normalizer = key_normalizer

    def normalize_key(self, raw):
        return self._key_normalizer(raw)

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return f'LoadedReferenceMap({self.name!r}, {len(self._data)} keys)'


def build_reference_map(declaration: ReferenceMap, pairs: Iterable) -> LoadedReferenceMap:
    """Build a loaded map from key and value pairs read for a declaration.

    Null keys are skipped (unless ``skip_null_keys`` is false). A normalized
    key that arrives with two different values is an error, because which one
    would win depends on the order the database returned the rows.
    """

    data = {}
    for raw_key, raw_value in pairs:
        if raw_key is None and declaration.skip_null_keys:
            continue
        key = declaration.normalize_key(raw_key)
        if key is None and declaration.skip_null_keys:
            continue
        value = declaration.normalize_value(raw_value)
        if key in data and data[key] != value:
            raise ReferenceMapError(
                f"Reference map '{declaration.name}': key {key!r} has two values, "
                f'{data[key]!r} and {value!r}'
            )
        data[key] = value
    return LoadedReferenceMap(declaration.name, data, declaration.normalize_key)
