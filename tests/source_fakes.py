"""Fake sources shared by the migration tests."""

from core.adapters.sql_source import SqlSourceAdapter
from core.dialects import FirebirdDialect, PostgresDialect

DIALECTS = {'postgres': PostgresDialect, 'firebird': FirebirdDialect}
LIMITS = {'postgres': 'LIMIT {}', 'firebird': 'ROWS {}'}


class FakeSqlSource(SqlSourceAdapter):
    """SQL source with the real translation of neutral queries and fixed rows.

    Each SQL sent to the source, the progress count included, goes to ``queries``.
    """

    def __init__(self, name='postgres', rows=()):
        self.name = name
        self.dialect = DIALECTS[name]()
        self.rows = list(rows)
        self.queries = []

    def limit_suffix(self, limit):
        return LIMITS[self.name].format(limit)

    def close(self):
        pass

    def _rows(self, sql):
        self.queries.append(sql)
        return iter(self.rows)

    def _scalar(self, sql):
        self.queries.append(sql)
        return len(self.rows)


def sql_writer(writes, target=None):
    """Engine writer that renders each operation with the PostgreSQL target into ``writes``."""

    from core.adapters.postgres import PostgresTargetAdapter

    target = target or PostgresTargetAdapter()
    return lambda operation: writes.extend(target.render(operation))
