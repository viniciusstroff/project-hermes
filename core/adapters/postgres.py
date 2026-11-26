import itertools
from typing import Any

from core.adapters.pg_dump import PgDumpCopy
from core.adapters.postgres_session import CAPABILITIES, PostgresSession
from core.adapters.sql_source import SqlSourceAdapter
from core.artifacts import ArtifactFormat
from core.connections import ConnectionConfigError
from core.dialects import PostgresDialect
from core.dialects.base import UnsupportedValueError
from core.operations import (
    Clear,
    Comment,
    Insert,
    Native,
    ResetIdentity,
    ResumeConstraints,
    SuspendConstraints,
    ValueSerializationError,
)
from core.queries import NativeAdapterMismatchError
from core.references import TargetReadClosedError
from core.sql import SqlLiteral
from core.versions import check_server_version, postgres_version, version_text

_cursor_counter = itertools.count(1)


# Keys of SOURCE_<NAME>_* and TARGET_* that every PostgreSQL connection needs.
REQUIRED_PARAMETERS = ('host', 'name', 'user')


def _connect(config):
    """Open a psycopg2 connection from a ConnectionConfig. The driver is imported only here."""

    import psycopg2

    config.require(*REQUIRED_PARAMETERS)
    return psycopg2.connect(
        host=config.get('host'),
        port=config.get('port', '5432'),
        dbname=config.get('name'),
        user=config.get('user'),
        password=config.get('pass'),
    )


class PostgresSourceAdapter(SqlSourceAdapter):
    """PostgreSQL source. Rows stream through a named (server-side) cursor."""

    name = 'postgres'
    # The real V1 runs 9.6.
    supported_versions = (9, 6)

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
        # Optional native copy of compatible entities (SOURCE_<NAME>_NATIVE_COPY).
        self.native_copy = None

    @classmethod
    def connect(cls, config):
        tool = config.get('native_copy')
        if tool not in (None, 'pg_dump'):
            raise ConnectionConfigError(
                f'{config.prefix}_NATIVE_COPY={tool} não existe no adapter postgres. Use pg_dump ou deixe vazio.'
            )
        adapter = cls(_connect(config))
        try:
            check_server_version(
                config.prefix, cls.name, 'source', postgres_version(getattr(adapter.connection, 'server_version', None)),
                cls.supported_versions,
            )
            if tool == 'pg_dump':
                native = PgDumpCopy(config, binary=config.get('pg_dump', 'pg_dump'))
                native.check_version(adapter.connection.server_version, adapter.server_version)
                adapter.native_copy = native
        except Exception:
            adapter.close()
            raise
        return adapter

    @property
    def server_version(self):
        return _server_version(self.connection)

    def limit_suffix(self, limit: int) -> str:
        return f'LIMIT {limit}'

    def close(self) -> None:
        self.connection.close()

    def _rows(self, sql: str):
        cursor = self.connection.cursor(
            f'{self.cursor_prefix}_{next(_cursor_counter)}',
            cursor_factory=self.cursor_factory,
        )
        cursor.itersize = self.itersize
        try:
            cursor.execute(sql)
            yield from cursor
        finally:
            cursor.close()

    def _scalar(self, sql: str) -> Any:
        return _first_column(self.connection, sql)


def _first_column(connection, sql: str) -> Any:
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        row = cursor.fetchone()
    finally:
        cursor.close()
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


class PostgresTargetAdapter:
    """PostgreSQL target: renders operations as SQL and reads reference data."""

    name = 'postgres'
    artifact_format = ArtifactFormat('sql', 'sql')
    capabilities = CAPABILITIES
    required_parameters = REQUIRED_PARAMETERS
    # pg_get_serial_sequence and the catalog queries of the target.
    supported_versions = (12,)

    def __init__(self, dialect=None, connection=None):
        self.dialect = dialect or PostgresDialect()
        self.connection = connection
        self._reader = None
        self._tables = None
        self._reads_closed = False

    @classmethod
    def connect(cls, config):
        adapter = cls(connection=_connect(config))
        try:
            check_server_version(
                config.prefix, cls.name, 'target', postgres_version(getattr(adapter.connection, 'server_version', None)),
                cls.supported_versions,
            )
        except Exception:
            adapter.close()
            raise
        return adapter

    @property
    def server_version(self):
        return _server_version(self.connection)

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()

    def session(self, waits_between_commands: bool = False):
        """Application session over the target connection. Closing it closes the connection."""
        return PostgresSession(self.connection, waits_between_commands=waits_between_commands)

    # Reading ---------------------------------------------------------------

    def read(self, query):
        self._ensure_reads_open()
        if self._reader is None:
            self._reader = PostgresSourceAdapter(self.connection, dialect=self.dialect, cursor_prefix='target')
        return self._reader.read(query)

    def close_reads(self) -> None:
        """Cache the table list of SuspendConstraints, then end the read transaction.

        psycopg2 opened a transaction on the first read. Ending it releases
        the ACCESS SHARE locks held on the tables and the catalog.
        """
        if self.connection is not None and not self._reads_closed:
            self._catalog_tables()
        self._reads_closed = True
        rollback = getattr(self.connection, 'rollback', None)
        if rollback is not None:
            rollback()

    def _ensure_reads_open(self):
        if self._reads_closed:
            raise TargetReadClosedError(
                'Target reads are closed: the target adapter cannot query the target '
                'after the reference maps are loaded'
            )

    def _catalog_tables(self) -> list:
        if self._tables is None:
            self._ensure_reads_open()
            if self.connection is None:
                raise RuntimeError('O adapter de destino postgres não tem conexão para ler o catálogo.')
            cursor = self.connection.cursor()
            try:
                cursor.execute(
                    'SELECT schemaname, tablename FROM pg_catalog.pg_tables '
                    "WHERE schemaname NOT IN ('information_schema', 'pg_catalog') "
                    'ORDER BY schemaname, tablename'
                )
                rows = [tuple(row.values()) if isinstance(row, dict) else tuple(row) for row in cursor]
            finally:
                cursor.close()
            self._tables = [f'{schema}.{table}' for schema, table in rows]
        return self._tables

    # Rendering -------------------------------------------------------------

    def render(self, operation) -> list:
        renderer = self._RENDERERS.get(type(operation))
        if renderer is None:
            raise TypeError(f'O adapter postgres não renderiza {type(operation).__name__}.')
        return renderer(self, operation)

    def _render_insert(self, operation: Insert) -> list:
        columns = ', '.join(self._quote(column) for column in operation.values)
        values = ', '.join(
            self._serialize(operation.entity, column, value) for column, value in operation.values.items()
        )
        return [f'INSERT INTO {self._quote(operation.entity)} ({columns}) VALUES ({values});\n']

    def _render_clear(self, operation: Clear) -> list:
        entities = ', '.join(self._quote(entity) for entity in operation.entities)
        return [f'TRUNCATE {entities} RESTART IDENTITY;\n']

    def _render_suspend(self, operation: SuspendConstraints) -> list:
        return [f'ALTER TABLE {self._quote(entity)} DISABLE TRIGGER ALL;\n' for entity in self._entities(operation)]

    def _render_resume(self, operation: ResumeConstraints) -> list:
        return [f'ALTER TABLE {self._quote(entity)} ENABLE TRIGGER ALL;\n' for entity in self._entities(operation)]

    def _render_reset_identity(self, operation: ResetIdentity) -> list:
        entity = self._quote(operation.entity)
        # The table argument is parsed like an identifier; the column one is taken literally.
        sequence = (
            f'pg_catalog.pg_get_serial_sequence({self.dialect.serialize_value(entity)}, '
            f'{self.dialect.serialize_value(operation.column)})'
        )
        highest = f'(SELECT MAX({self._quote(operation.column)}) FROM {entity})'
        return [f'SELECT pg_catalog.setval({sequence}, COALESCE({highest}, 1));\n']

    def _render_native(self, operation: Native) -> list:
        if operation.adapter != self.name:
            raise NativeAdapterMismatchError('A operação Native', operation.adapter, self.name)
        if not isinstance(operation.payload, str):
            raise TypeError(f'Native do adapter postgres deve ser texto SQL; recebeu {type(operation.payload).__name__}.')
        payload = operation.payload
        return [payload if payload.endswith('\n') else payload + '\n']

    def _render_comment(self, operation: Comment) -> list:
        rule = '-----' + '-' * len(operation.text)
        return [f'\n{rule}\n-----{operation.text}\n{rule}\n\n']

    _RENDERERS = {
        Insert: _render_insert,
        Clear: _render_clear,
        SuspendConstraints: _render_suspend,
        ResumeConstraints: _render_resume,
        ResetIdentity: _render_reset_identity,
        Native: _render_native,
        Comment: _render_comment,
    }

    def _entities(self, operation) -> list:
        return list(operation.entities) or self._catalog_tables()

    def _quote(self, identifier: str) -> str:
        return self.dialect.quote_identifier(identifier)

    def _serialize(self, entity: str, column: str, value) -> str:
        if isinstance(value, SqlLiteral):
            return value.sql
        try:
            return self.dialect.serialize_value(value)
        except UnsupportedValueError as exc:
            raise ValueSerializationError(entity, column, type(value).__name__, str(exc)) from exc


def _server_version(connection):
    """Version text from the libpq number: 90624 is 9.6.24 and 160004 is 16.4."""

    version = postgres_version(getattr(connection, 'server_version', None))
    return version_text(version) if version else None
