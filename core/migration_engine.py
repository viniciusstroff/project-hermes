from core.operations import Insert
from core.queries import UnsupportedQueryError, with_limit

DEFAULT_PROGRESS_EVERY = 1000
BINARY_TYPES = (bytes, bytearray, memoryview)


class MigrationEngine:
    """Contexto de execução compartilhado entre TableMigrations."""

    def __init__(
        self,
        source_adapter,
        target_adapter,
        writer,
        limit: int | None = None,
        progress_fn=None,
        progress_every: int = DEFAULT_PROGRESS_EVERY,
        warn_fn=None,
    ):
        self.source_adapter = source_adapter
        self.target_adapter = target_adapter
        self.writer = writer
        # Debug mode: the context applies this row limit to every query.
        self.limit = limit
        self.progress_fn = progress_fn
        self.progress_every = progress_every if progress_every and progress_every > 0 else DEFAULT_PROGRESS_EVERY
        # warn_fn(key, message) logs a message once per key.
        self.warn_fn = warn_fn

    def read(self, query):
        """Rows of a SourceQuery or NativeQuery, with the debug limit applied."""
        return self.source_adapter.read(with_limit(query, self.limit))

    def count(self, query):
        return self.source_adapter.count(with_limit(query, self.limit))

    def total_rows(self, query):
        """Row count for the progress, or None when the source cannot count.

        A query the adapter refuses is not a counting problem: the error goes up
        before anything is read.
        """
        try:
            total = self.count(query)
        except UnsupportedQueryError:
            raise
        except Exception:
            return None
        return None if total is None else int(total)

    def emit(self, operation):
        """Deliver one write operation to the output, which renders it for the target."""
        self.writer(operation)

    def insert(self, entity: str, pairs):
        """Emit an Insert from (column, Python value) pairs, in the order of the fields."""

        values = {}
        for column, value in pairs:
            if column in values:
                raise ValueError(f'{entity}: a coluna {column} recebe valor de mais de um campo.')
            values[column] = value
            if isinstance(value, BINARY_TYPES) and self.warn_fn is not None:
                # A text column also accepts bytea, storing the hexadecimal form.
                self.warn_fn(
                    f'binary:{entity}.{column}',
                    f'{entity}.{column} recebeu valor binário. Se a coluna é de texto, decodifique com Transform.',
                )
        self.emit(Insert(entity, values))

    def report_progress(self, table: str, count: int, total: int = None, done: bool = False):
        if self.progress_fn is not None:
            self.progress_fn(table, count, total, done)
