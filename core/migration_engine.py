import itertools
import re

import psycopg2
import psycopg2.extras

_cursor_counter = itertools.count(1)
DEFAULT_PROGRESS_EVERY = 1000


class FirebirdCursorAdapter:
    """Adapta cursor Firebird para a interface esperada pelo TableMigration."""

    def __init__(self, cursor):
        self.cursor = cursor
        self.columns = []

    def execute(self, sql):
        self.cursor.execute(sql)
        self.columns = [col[0].lower() for col in self.cursor.description]

    def __iter__(self):
        for row in self.cursor:
            if isinstance(row, dict):
                yield row
            else:
                yield dict(zip(self.columns, row))


class MigrationEngine:
    """Contexto de execução compartilhado entre TableMigrations."""

    def __init__(
        self,
        v1_conn,
        writer,
        limit: str = '',
        progress_fn=None,
        progress_every: int = DEFAULT_PROGRESS_EVERY,
        source: str = 'postgres',
    ):
        self.v1_conn = v1_conn
        self.writer = writer
        self.limit = limit
        self.progress_fn = progress_fn
        self.progress_every = progress_every if progress_every and progress_every > 0 else DEFAULT_PROGRESS_EVERY
        self.source = source

    @property
    def limit_clause(self) -> str:
        if self.source != 'firebird' or not self.limit:
            return self.limit

        match = re.fullmatch(r'\s*LIMIT\s+(\d+)\s*', self.limit, flags=re.IGNORECASE)
        if match:
            return f'ROWS {match.group(1)}'
        return self.limit

    def v1_cursor(self, factory=psycopg2.extras.RealDictCursor):
        if self.source == 'firebird':
            return FirebirdCursorAdapter(self.v1_conn.cursor())

        cur = self.v1_conn.cursor(f'engine_{next(_cursor_counter)}', cursor_factory=factory)
        cur.itersize = 2000
        return cur

    def write_sql(self, sql: str):
        self.writer(sql)

    def scalar(self, sql: str):
        cur = self.v1_conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            return next(iter(row.values()))
        return row[0]

    def report_progress(self, table: str, count: int, total: int = None, done: bool = False):
        if self.progress_fn is not None:
            self.progress_fn(table, count, total, done)
