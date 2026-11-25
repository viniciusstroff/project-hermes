import re
from typing import Any


class PostgresDialect:
    """PostgreSQL SQL rendering rules used by the migration target."""

    name = 'postgres'

    def serialize_value(self, value: Any) -> str:
        if value is None:
            return 'NULL'
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, (int, float)):
            return str(value)

        escaped = str(value).replace("'", "''")
        if '\\' in escaped:
            # PostgreSQL always reads a backslash inside E'...' as an escape,
            # whatever standard_conforming_strings says. Doubling it keeps the
            # text unchanged in the target.
            return "E'" + escaped.replace('\\', '\\\\') + "'"
        return "'" + escaped + "'"

    def quote_identifier(self, identifier: str) -> str:
        parts = identifier.split('.')
        return '.'.join(self._quote_part(part) for part in parts)

    def limit_clause(self, limit: str) -> str:
        return limit or ''

    def _quote_part(self, identifier: str) -> str:
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', identifier):
            return identifier
        return '"' + identifier.replace('"', '""') + '"'
