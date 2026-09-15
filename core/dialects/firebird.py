import re


class FirebirdDialect:
    """Firebird SQL rendering rules used by source queries."""

    def serialize_value(self, value) -> str:
        if value is None:
            return 'NULL'
        if isinstance(value, bool):
            return '1' if value else '0'
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    def quote_identifier(self, identifier: str) -> str:
        parts = identifier.split('.')
        return '.'.join('"' + part.replace('"', '""') + '"' for part in parts)

    def limit_clause(self, limit: str) -> str:
        if not limit:
            return ''

        match = re.fullmatch(r'\s*LIMIT\s+(\d+)\s*', limit, flags=re.IGNORECASE)
        if match:
            return f'ROWS {match.group(1)}'
        return limit
