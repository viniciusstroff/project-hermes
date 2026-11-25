from core.dialects.base import UnsupportedValueError
from core.sql import SqlLiteral

DEFAULT_PROGRESS_EVERY = 1000
BINARY_TYPES = (bytes, bytearray, memoryview)


class ValueSerializationError(UnsupportedValueError):
    """A field value the target cannot represent, with the place it came from.

    The message names the target table, the column and the Python type, and
    leaves the value out: it goes to the log and to the manifest.
    """

    def __init__(self, table: str, column: str, python_type: str, reason: str):
        self.table = table
        self.column = column
        self.python_type = python_type
        self.reason = reason
        super().__init__(f'{table}.{column} ({python_type}): {reason}')


class MigrationEngine:
    """Contexto de execução compartilhado entre TableMigrations."""

    def __init__(
        self,
        source_adapter,
        target_adapter,
        writer,
        limit: str = '',
        progress_fn=None,
        progress_every: int = DEFAULT_PROGRESS_EVERY,
        warn_fn=None,
    ):
        self.source_adapter = source_adapter
        self.target_adapter = target_adapter
        self.writer = writer
        self.limit = limit
        self.progress_fn = progress_fn
        self.progress_every = progress_every if progress_every and progress_every > 0 else DEFAULT_PROGRESS_EVERY
        # warn_fn(key, message) logs a message once per key.
        self.warn_fn = warn_fn

    @property
    def limit_clause(self) -> str:
        return self.source_adapter.limit_clause(self.limit)

    def v1_cursor(self):
        return self.source_adapter.cursor()

    def write_sql(self, sql: str):
        self.writer(sql)

    def scalar(self, sql: str):
        return self.source_adapter.scalar(sql)

    def insert_statement(self, table: str, columns, values) -> str:
        return self.target_adapter.insert_statement(table, columns, values)

    def render_value(self, table: str, column: str, value) -> SqlLiteral:
        """Serialize one field value through the target adapter, naming the column on failure."""

        try:
            rendered = self.target_adapter.serialize_value(value)
        except UnsupportedValueError as exc:
            raise ValueSerializationError(table, column, type(value).__name__, str(exc)) from exc
        if isinstance(value, BINARY_TYPES) and self.warn_fn is not None:
            # A text column also accepts bytea, storing the hexadecimal form.
            self.warn_fn(
                f'binary:{table}.{column}',
                f'{table}.{column} recebeu valor binário. Se a coluna é de texto, decodifique com Transform.',
            )
        return SqlLiteral(rendered)

    def report_progress(self, table: str, count: int, total: int = None, done: bool = False):
        if self.progress_fn is not None:
            self.progress_fn(table, count, total, done)
