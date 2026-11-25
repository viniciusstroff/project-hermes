from typing import Any

from core.adapters.sql_source import SqlSourceAdapter
from core.dialects import FirebirdDialect


class FirebirdSourceAdapter(SqlSourceAdapter):
    """Firebird source. Rows come as dictionaries with lower-case column names."""

    name = 'firebird'

    def __init__(self, connection, *, dialect=None):
        self.connection = connection
        self.dialect = dialect or FirebirdDialect()

    @classmethod
    def connect(cls, config):
        """Open a Firebird connection from a ConnectionConfig. The driver is imported only here."""

        import firebirdsql

        config.require('host', 'name', 'user')
        options = {
            'host': config.get('host'),
            'port': int(config.get('port', '3050')),
            'database': config.get('name'),
            'user': config.get('user'),
            'password': config.get('pass'),
        }
        if config.get('charset'):
            options['charset'] = config.get('charset')
        return cls(firebirdsql.connect(**options))

    def limit_suffix(self, limit: int) -> str:
        # ROWS exists since Firebird 2.0; LIMIT does not exist.
        return f'ROWS {limit}'

    def close(self) -> None:
        self.connection.close()

    def _rows(self, sql: str):
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql)
            # Firebird returns unquoted names in upper case.
            columns = [description[0].lower() for description in cursor.description]
            for row in cursor:
                yield row if isinstance(row, dict) else dict(zip(columns, row))
        finally:
            cursor.close()

    def _scalar(self, sql: str) -> Any:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql)
            row = cursor.fetchone()
        finally:
            cursor.close()
        return None if row is None else row[0]
