import itertools
from typing import Any, Sequence

from core.dialects import PostgresDialect
from core.sql import SqlLiteral

_cursor_counter = itertools.count(1)


def _connect(config):
    """Open a psycopg2 connection from a ConnectionConfig. The driver is imported only here."""

    import psycopg2

    config.require('host', 'name', 'user')
    return psycopg2.connect(
        host=config.get('host'),
        port=config.get('port', '5432'),
        dbname=config.get('name'),
        user=config.get('user'),
        password=config.get('pass'),
    )


class PostgresSourceAdapter:
    """PostgreSQL source adapter preserving named cursor streaming behavior."""

    def __init__(
        self,
        connection,
        *,
        cursor_factory=None,
        cursor_prefix: str = 'source',
        itersize: int = 2000,
        dialect=None,
    ):
        if cursor_factory is None:
            import psycopg2.extras

            cursor_factory = psycopg2.extras.RealDictCursor

        self.connection = connection
        self.cursor_factory = cursor_factory
        self.cursor_prefix = cursor_prefix
        self.itersize = itersize
        self.dialect = dialect or PostgresDialect()

    @classmethod
    def connect(cls, config):
        return cls(_connect(config))

    def cursor(self):
        cur = self.connection.cursor(
            f'{self.cursor_prefix}_{next(_cursor_counter)}',
            cursor_factory=self.cursor_factory,
        )
        cur.itersize = self.itersize
        return cur

    def scalar(self, sql: str) -> Any:
        cur = self.connection.cursor()
        try:
            cur.execute(sql)
            row = cur.fetchone()
        finally:
            if hasattr(cur, 'close'):
                cur.close()

        if row is None:
            return None
        if isinstance(row, dict):
            return next(iter(row.values()))
        return row[0]

    def limit_clause(self, limit: str) -> str:
        return self.dialect.limit_clause(limit)

    def close(self) -> None:
        self.connection.close()


class PostgresTargetAdapter:
    """PostgreSQL target adapter for generated INSERT statements."""

    def __init__(self, dialect=None, connection=None):
        self._dialect = dialect or PostgresDialect()
        self.connection = connection
        self._metadata = None

    @classmethod
    def connect(cls, config):
        return cls(connection=_connect(config))

    @property
    def dialect(self):
        return self._dialect

    @property
    def metadata(self):
        """Metadata provider over the target connection."""
        if self._metadata is None and self.connection is not None:
            from core.metadata import PostgresMetadataProvider

            self._metadata = PostgresMetadataProvider(self.connection, self._dialect)
        return self._metadata

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()

    def serialize_value(self, value: Any) -> str:
        if isinstance(value, SqlLiteral):
            return value.sql
        return self.dialect.serialize_value(value)

    def insert_statement(
        self,
        table: str,
        columns: Sequence[str],
        values: Sequence[Any],
    ) -> str:
        insert_cols = ', '.join(columns)
        insert_vals = ', '.join(self.serialize_value(value) for value in values)
        return f'INSERT INTO {table} ({insert_cols}) VALUES ({insert_vals});\n'
