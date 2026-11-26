"""Shared translation of neutral queries for relational source adapters.

A subclass sets ``name`` and ``dialect``, renders the row limit and runs the
SQL through its driver (``_rows`` and ``_scalar``). Everything else, from the
field list to the refusal of native pieces of another adapter, is the same for
any SQL database.
"""

from abc import ABC, abstractmethod
from typing import Any, Iterator, Mapping

from core.adapters.ownership import check_owner
from core.queries import NativeField, NativeQuery, SourceQuery, UnsupportedQueryError, check_native


class SqlSourceAdapter(ABC):
    """Base of the source adapters that read with SQL."""

    name: str
    dialect: Any

    def read(self, query) -> Iterator[Mapping[str, Any]]:
        check_owner(self)
        # Render now, so a refused query fails at the call and not at the first row.
        return self._rows(self.render(query))

    def count(self, query) -> int | None:
        check_owner(self)
        total = self._scalar(f'SELECT COUNT(*) FROM ({self.render(query)}) progress_src')
        return None if total is None else int(total)

    def render(self, query) -> str:
        """SQL of a SourceQuery or of a NativeQuery written for this adapter."""

        if isinstance(query, NativeQuery):
            return self._render_native(query)
        if isinstance(query, SourceQuery):
            return self._render_source(query)
        raise UnsupportedQueryError(f'O adapter {self.name} não lê {type(query).__name__}.')

    @abstractmethod
    def limit_suffix(self, limit: int) -> str:
        """Clause that limits the rows of a SELECT, such as ``LIMIT 10``."""

    @abstractmethod
    def _rows(self, sql: str) -> Iterator[Mapping[str, Any]]:
        """Run the SQL and yield one mapping per row, keyed by column name."""

    @abstractmethod
    def _scalar(self, sql: str) -> Any:
        """Run the SQL and return the first column of the first row."""

    def _render_native(self, query: NativeQuery) -> str:
        check_native(query, self.name)
        if not isinstance(query.query, str):
            raise UnsupportedQueryError(
                f'A NativeQuery do adapter {self.name} deve ser texto SQL; recebeu {type(query.query).__name__}.'
            )
        sql = query.query.strip().rstrip(';').strip()
        if query.limit is None:
            return sql
        return f'SELECT * FROM ({sql}) native_src {self.limit_suffix(query.limit)}'

    def _render_source(self, query: SourceQuery) -> str:
        # Check every native field first: nothing is rendered for a refused query.
        for item in query.fields:
            check_native(item, self.name)
        columns = ', '.join(self._select_item(item) for item in query.fields) or '*'
        sql = f'SELECT {columns} FROM {self._entity(query.entity)}'
        if query.where:
            sql += ' WHERE ' + ' AND '.join(self._condition(key, value) for key, value in query.where.items())
        if query.order_by:
            sql += ' ORDER BY ' + ', '.join(self._column(column) for column in query.order_by)
        if query.limit is not None:
            sql += ' ' + self.limit_suffix(query.limit)
        return sql

    def _select_item(self, item) -> str:
        if isinstance(item, NativeField):
            return f'{item.expression} AS {self._column(item.alias)}'
        return self._column(item)

    def _condition(self, key, value) -> str:
        column = self._column(key)
        if value is None:
            return f'{column} IS NULL'
        return f'{column} = {self.dialect.serialize_value(value)}'

    def _entity(self, entity: str) -> str:
        return self.dialect.quote_identifier(entity)

    def _column(self, column) -> str:
        if not isinstance(column, str):
            raise UnsupportedQueryError(f'Campo inválido para o adapter {self.name}: {column!r}.')
        if '.' in column:
            raise UnsupportedQueryError(
                f"O adapter {self.name} não lê campo aninhado ('{column}'). "
                'Use uma NativeQuery com a junção ou o caminho no SQL.'
            )
        return self.dialect.quote_identifier(column)
