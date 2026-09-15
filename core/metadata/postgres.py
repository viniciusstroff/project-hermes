from typing import Any, Callable


class PostgresMetadataProvider:
    """PostgreSQL-backed provider for target metadata operations."""

    def __init__(self, connection):
        self.connection = connection
        self._trigger_sql = None

    def load_reference_map(
        self,
        sql: str,
        *,
        key_normalizer: Callable[[Any], Any] | None = None,
        value_normalizer: Callable[[Any], Any] | None = None,
        skip_null_keys: bool = True,
    ) -> dict[Any, Any]:
        key_normalizer = key_normalizer or (lambda value: str(value).strip())
        value_normalizer = value_normalizer or (lambda value: value)

        ref_map = {}
        cur = self.connection.cursor()
        try:
            cur.execute(sql)
            for raw_key, raw_value in cur:
                if raw_key is None and skip_null_keys:
                    continue
                key = key_normalizer(raw_key)
                if key is None and skip_null_keys:
                    continue
                ref_map[key] = value_normalizer(raw_value)
        finally:
            if hasattr(cur, 'close'):
                cur.close()

        return ref_map

    def trigger_commands_sql(self) -> tuple[str, str]:
        if self._trigger_sql is None:
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
