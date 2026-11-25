"""Neutral description of what a migration reads from a source.

``SourceQuery`` covers the common case in any database: one entity (table,
collection), the fields, equality filters, ordering and a row limit. What does
not fit, such as a join or a MongoDB pipeline, goes in ``NativeQuery``, bound
to the adapter that understands it. A field computed at the source goes in
``NativeField``, also bound to one adapter. The source adapter refuses native
pieces written for another adapter before it reads anything.
"""

from dataclasses import dataclass, field, replace
from typing import Any, Mapping


class UnsupportedQueryError(ValueError):
    """The source adapter cannot run this query. Raised before anything is read."""


class NativeAdapterMismatchError(UnsupportedQueryError):
    """A native query or field was written for another adapter."""

    def __init__(self, what: str, declared: str, found: str):
        self.declared = declared
        self.found = found
        super().__init__(
            f"{what} foi escrito para o adapter '{declared}', mas a origem usa o adapter '{found}'."
        )


@dataclass(frozen=True)
class NativeField:
    """A field computed at the source in the language of one adapter.

    It is also a field strategy: it selects the expression under ``alias``
    and writes the value read under that name to the target column ``alias``.

    Ex: NativeField('postgres', "NULLIF(f_cpf, '')", 'f_cpf')
    """

    adapter: str
    expression: Any
    alias: str

    @property
    def select_columns(self):
        return [self]

    @property
    def insert_col(self):
        return self.alias

    def raw_value(self, row):
        return row[self.alias]


@dataclass(frozen=True)
class SourceQuery:
    """Rows of one entity, with equality filters, ordering and a row limit.

    Empty ``fields`` reads the fields the migration strategies ask for, or
    every field when the query runs on its own.
    """

    entity: str
    fields: tuple = ()
    where: Mapping[str, Any] = field(default_factory=dict)
    order_by: tuple = ()
    limit: int | None = None

    def __post_init__(self):
        if not isinstance(self.entity, str) or not self.entity.strip():
            raise TypeError(f'SourceQuery precisa de uma entidade; recebeu {self.entity!r}.')
        object.__setattr__(self, 'fields', _as_tuple(self.fields))
        object.__setattr__(self, 'order_by', _as_tuple(self.order_by))
        object.__setattr__(self, 'where', dict(self.where or {}))
        _check_limit(self.limit)


@dataclass(frozen=True)
class NativeQuery:
    """A query in the language of one adapter: SQL text, a MongoDB pipeline...

    The adapter applies ``limit`` around the query, so the text has no limit
    placeholder.
    """

    adapter: str
    query: Any
    limit: int | None = None

    def __post_init__(self):
        if not isinstance(self.adapter, str) or not self.adapter:
            raise TypeError(f'NativeQuery precisa do nome do adapter; recebeu {self.adapter!r}.')
        _check_limit(self.limit)


def check_source(source):
    """Refuse anything that is not a SourceQuery or a NativeQuery, at construction."""

    if not isinstance(source, (SourceQuery, NativeQuery)):
        raise TypeError(
            f'source deve ser SourceQuery ou NativeQuery; recebeu {type(source).__name__}. '
            "SQL escrito à mão vai em NativeQuery('<adapter>', sql)."
        )
    return source


def with_default_fields(query, fields):
    """A SourceQuery without fields reads the given ones. Other queries are unchanged."""

    if isinstance(query, SourceQuery) and not query.fields and fields:
        return replace(query, fields=tuple(fields))
    return query


def with_limit(query, limit: int | None):
    """Apply a run-wide limit (debug mode) on top of the query limit, keeping the smaller."""

    if limit is None or (query.limit is not None and query.limit <= limit):
        return query
    return replace(query, limit=limit)


def check_native(item, adapter_name: str):
    """Refuse a NativeQuery or a NativeField declared for another adapter."""

    if isinstance(item, NativeQuery) and item.adapter != adapter_name:
        raise NativeAdapterMismatchError('A NativeQuery', item.adapter, adapter_name)
    if isinstance(item, NativeField) and item.adapter != adapter_name:
        raise NativeAdapterMismatchError(f"O NativeField '{item.alias}'", item.adapter, adapter_name)


def _as_tuple(value):
    if value is None:
        return ()
    if isinstance(value, (str, NativeField)):
        return (value,)
    return tuple(value)


def _check_limit(limit):
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        raise TypeError(f'O limite deve ser um inteiro não negativo ou None; recebeu {limit!r}.')
