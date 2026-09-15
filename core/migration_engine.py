DEFAULT_PROGRESS_EVERY = 1000


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
    ):
        self.source_adapter = source_adapter
        self.target_adapter = target_adapter
        self.writer = writer
        self.limit = limit
        self.progress_fn = progress_fn
        self.progress_every = progress_every if progress_every and progress_every > 0 else DEFAULT_PROGRESS_EVERY

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

    def report_progress(self, table: str, count: int, total: int = None, done: bool = False):
        if self.progress_fn is not None:
            self.progress_fn(table, count, total, done)
