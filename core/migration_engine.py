from contextlib import nullcontext

from core.metrics import NULL_UNIT, LostValue
from core.operations import Insert
from core.queries import UnsupportedQueryError, with_limit
from core.strategies import FieldStrategy

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
        metrics=None,
        source_name: str = None,
        cancel_check=None,
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
        # GenerationMetrics of the run, or None (an engine built by hand counts nothing).
        self.metrics = metrics
        self.source_name = source_name
        # In a worker, raises when another task failed or the run was interrupted.
        self.cancel_check = cancel_check

    def unit(self, label: str):
        """Context manager of one migration unit: rows read, inserts and losses of the block."""

        if self.metrics is None:
            return nullcontext(NULL_UNIT)
        return self.metrics.unit(label, source=self.source_name)

    def field_values(self, entity: str, fields):
        """Function from a source row to the (column, value) pairs of an Insert into ``entity``.

        Built once per migration run, so the per-row work stays the strategy
        itself. A strategy that overrides ``resolve`` may report a lost value:
        the function records it in the open unit and keeps the value of the
        ``LostValue``. The others, client strategies without ``resolve``
        included, are read with ``raw_value``.
        """

        readers = [(field.insert_col, self._field_reader(entity, field)) for field in fields]
        return lambda row: [(column, read(row)) for column, read in readers]

    def _field_reader(self, entity: str, field):
        resolve = getattr(type(field), 'resolve', None)
        if resolve is None or resolve is FieldStrategy.resolve:
            return field.raw_value

        resolve = field.resolve
        lost = self.metrics.lost if self.metrics is not None else None
        column, strategy = field.insert_col, type(field).__name__

        def read(row):
            value = resolve(row)
            # An exact type check: this runs for every field of every row.
            if type(value) is LostValue:
                if lost is not None:
                    lost(entity, column, strategy, value)
                return value.value
            return value

        return read

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

        if self.cancel_check is not None:
            self.cancel_check()
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
        if self.metrics is not None:
            self.metrics.inserted(entity)

    def report_progress(self, table: str, count: int, total: int = None, done: bool = False):
        if self.progress_fn is not None:
            self.progress_fn(table, count, total, done)
