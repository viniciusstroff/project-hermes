import datetime
import math
import re
import uuid
from decimal import Decimal
from typing import Any

from core.dialects.base import UnsupportedValueError

BINARY_TYPES = (bytes, bytearray, memoryview)


class PostgresDialect:
    """PostgreSQL SQL rendering rules used by the migration target."""

    name = 'postgres'

    def serialize_value(self, value: Any) -> str:
        """Render a value of a closed list of Python types as a PostgreSQL literal.

        Any other type, and text with a NUL character, raises
        UnsupportedValueError instead of reaching the target as wrong data.
        """
        if value is None:
            return 'NULL'
        # bool before int, and datetime before date: they are subclasses.
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return self._serialize_float(value)
        if isinstance(value, Decimal):
            return self._serialize_decimal(value)
        if isinstance(value, str):
            return self._serialize_text(value)
        if isinstance(value, datetime.datetime):
            return self._serialize_text(value.isoformat(sep=' '))
        if isinstance(value, (datetime.date, datetime.time)):
            return self._serialize_text(value.isoformat())
        if isinstance(value, uuid.UUID):
            return self._serialize_text(str(value))
        if isinstance(value, BINARY_TYPES):
            # Hexadecimal text has no backslash, so it does not depend on
            # standard_conforming_strings.
            return f"decode('{bytes(value).hex()}', 'hex')"
        raise UnsupportedValueError(f'type {type(value).__name__} is not supported by the PostgreSQL dialect')

    @staticmethod
    def _serialize_float(value: float) -> str:
        if math.isnan(value):
            return "'NaN'::float8"
        if math.isinf(value):
            return "'Infinity'::float8" if value > 0 else "'-Infinity'::float8"
        return str(value)

    @staticmethod
    def _serialize_decimal(value: Decimal) -> str:
        if value.is_nan():
            return "'NaN'::numeric"
        if value.is_infinite():
            return "'Infinity'::numeric" if value > 0 else "'-Infinity'::numeric"
        # Finite values may use an exponent (1E+2), a valid PostgreSQL numeric constant.
        return str(value)

    @staticmethod
    def _serialize_text(value: str) -> str:
        position = value.find('\x00')
        if position >= 0:
            raise UnsupportedValueError(
                f'text has a NUL character at position {position}, which PostgreSQL does not store in text'
            )
        escaped = value.replace("'", "''")
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
        # PostgreSQL folds unquoted names to lower case, so any upper-case
        # letter needs quotes to keep pointing at the same column.
        if re.fullmatch(r'[a-z_][a-z0-9_]*', identifier):
            return identifier
        return '"' + identifier.replace('"', '""') + '"'
