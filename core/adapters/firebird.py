from typing import Any

from core.adapters.sql_source import SqlSourceAdapter
from core.dialects import FirebirdDialect
from core.versions import UnsupportedVersionError, check_server_version, parse_version

ENGINE_VERSION_SQL = "SELECT rdb$get_context('SYSTEM', 'ENGINE_VERSION') FROM rdb$database"


class FirebirdSourceAdapter(SqlSourceAdapter):
    """Firebird source. Rows come as dictionaries with lower-case column names."""

    name = 'firebird'
    # ENGINE_VERSION exists since 2.1 and ROWS since 2.0.
    supported_versions = (2, 5)

    def __init__(self, connection, *, dialect=None, server_version=None):
        self.connection = connection
        self.dialect = dialect or FirebirdDialect()
        self.server_version = server_version

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
        connection = firebirdsql.connect(**options)
        try:
            version = _engine_version(connection, config.prefix)
            check_server_version(config.prefix, cls.name, 'source', parse_version(version), cls.supported_versions)
        except Exception:
            connection.close()
            raise
        return cls(connection, server_version=version)

    def limit_suffix(self, limit: int) -> str:
        # ROWS exists in every supported version (2.0 and later); LIMIT does not exist.
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


def _engine_version(connection, prefix: str):
    """ENGINE_VERSION of the server, such as ``3.0.10``. None when the server answers nothing."""

    cursor = connection.cursor()
    try:
        cursor.execute(ENGINE_VERSION_SQL)
        row = cursor.fetchone()
    except Exception as exc:
        raise UnsupportedVersionError(
            f'Não foi possível ler a versão do servidor de {prefix}: {exc}. '
            'O adapter firebird aceita origem a partir da versão 2.5.'
        ) from exc
    finally:
        cursor.close()
    return str(row[0]).strip() if row and row[0] else None
