from typing import Any

from core.dialects import FirebirdDialect


class FirebirdCursorAdapter:
    """Adapt a Firebird cursor so rows are yielded as lower-case-key dictionaries."""

    def __init__(self, cursor):
        self.cursor = cursor
        self.columns = []

    def execute(self, sql: str) -> None:
        self.cursor.execute(sql)
        self.columns = [col[0].lower() for col in self.cursor.description]

    def __iter__(self):
        for row in self.cursor:
            if isinstance(row, dict):
                yield row
            else:
                yield dict(zip(self.columns, row))


class FirebirdSourceAdapter:
    """Firebird source adapter preserving existing row adaptation behavior."""

    def __init__(self, connection, *, dialect=None):
        self.connection = connection
        self.dialect = dialect or FirebirdDialect()

    def cursor(self):
        return FirebirdCursorAdapter(self.connection.cursor())

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
