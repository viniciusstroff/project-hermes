from typing import Any, Iterable

from core.dialects import PostgresDialect
from core.references import TargetReadClosedError


class PostgresMetadataProvider:
    """PostgreSQL-backed provider for target metadata operations."""

    def __init__(self, connection, dialect=None):
        self.connection = connection
        self.dialect = dialect or PostgresDialect()
        self._trigger_sql = None
        self._reads_closed = False

    def load_reference_pairs(self, reference) -> Iterable[tuple[Any, Any]]:
        self._ensure_reads_open()
        sql = reference.query if reference.query is not None else self._reference_select(reference)
        cur = self.connection.cursor()
        try:
            cur.execute(sql)
            return [self._pair(row) for row in cur]
        finally:
            if hasattr(cur, 'close'):
                cur.close()

    def close_reads(self) -> None:
        self._reads_closed = True
        # psycopg2 opened a transaction on the first read. Ending it releases
        # the ACCESS SHARE locks held on the tables and the catalog.
        if hasattr(self.connection, 'rollback'):
            self.connection.rollback()

    def _reference_select(self, reference) -> str:
        quote = self.dialect.quote_identifier
        sql = f'SELECT {quote(reference.key)}, {quote(reference.value)} FROM {quote(reference.table)}'
        conditions = [
            f'{quote(column)} IS NULL' if value is None
            else f'{quote(column)} = {self.dialect.serialize_value(value)}'
            for column, value in reference.filters.items()
        ]
        if conditions:
            sql += ' WHERE ' + ' AND '.join(conditions)
        return sql

    @staticmethod
    def _pair(row):
        if isinstance(row, dict):
            values = list(row.values())
            return values[0], values[1]
        return row[0], row[1]

    def _ensure_reads_open(self):
        if self._reads_closed:
            raise TargetReadClosedError(
                'Target reads are closed: the metadata provider cannot query the target '
                'after the reference maps are loaded'
            )

    def trigger_commands_sql(self) -> tuple[str, str]:
        if self._trigger_sql is None:
            self._ensure_reads_open()
            disable = []
            enable = []
            cur = self.connection.cursor()
            try:
                cur.execute(
                    "SELECT 'ALTER TABLE ' || schemaname || '.' || tablename || ' DISABLE TRIGGER ALL;' as disable_trigger, "
                    "'ALTER TABLE ' || schemaname || '.' || tablename || ' ENABLE TRIGGER ALL;' as enable_trigger "
                    "FROM pg_catalog.pg_tables "
                    "WHERE schemaname <> 'information_schema' AND schemaname <> 'pg_catalog' "
                    "ORDER BY schemaname, tablename;"
                )
                for row in cur:
                    if isinstance(row, dict):
                        disable_sql = row['disable_trigger']
                        enable_sql = row['enable_trigger']
                    else:
                        disable_sql = row[0]
                        enable_sql = row[1]
                    disable.append(disable_sql)
                    enable.append(enable_sql)
            finally:
                if hasattr(cur, 'close'):
                    cur.close()

            self._trigger_sql = (
                ''.join(command + '\n' for command in disable),
                ''.join(command + '\n' for command in enable),
            )

        return self._trigger_sql

    def disable_triggers_sql(self) -> str:
        return self.trigger_commands_sql()[0]

    def enable_triggers_sql(self) -> str:
        return self.trigger_commands_sql()[1]

    def disable_indexes_sql(self, indexes: list[str]) -> str:
        return ''.join(
            "UPDATE pg_index SET indisready=false WHERE indrelid = "
            "(SELECT oid FROM pg_class WHERE relname='{index}');\n".format(index=index)
            for index in indexes
        )

    def enable_indexes_sql(self, indexes: list[str]) -> str:
        return ''.join(
            "UPDATE pg_index SET indisready=true WHERE indrelid = "
            "(SELECT oid FROM pg_class WHERE relname='{index}');\n".format(index=index)
            for index in indexes
        )

    def reset_sequence_sql(self, table: str, column: str = 'f_id') -> str:
        return (
            "SELECT pg_catalog.setval('{table}_{column}_seq', "
            "COALESCE((SELECT MAX({column}) FROM public.{table}), 1));\n"
        ).format(table=table, column=column)

    def reset_sequences_sql(self, tables: list[str], column: str = 'f_id') -> str:
        return ''.join(self.reset_sequence_sql(table, column) for table in tables)
