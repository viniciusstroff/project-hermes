import re

# Firebird key words, context variables included. Unquoted, "user" is the
# session user and "date" is a syntax error. Firebird stores unquoted names in
# upper case, so quoting a simple name in upper case points at the same column
# and the list can err on the side of more words.
RESERVED_WORDS = frozenset('''
    add admin all alter and any as at avg begin between bigint bit_length blob both by case cast char
    char_length character character_length check close collate column commit connect constraint count
    create cross current current_connection current_date current_role current_time current_timestamp
    current_transaction current_user cursor date day dec decimal declare default delete deleting
    disconnect distinct double drop else end escape execute exists external extract false fetch filter
    float for foreign from full function gdscode global grant group having hour in index inner
    insensitive insert inserting int integer into is join leading left like long lower max merge min
    minute month national natural nchar no not null numeric octet_length of on only open or order outer
    parameter plan position post_event precision primary procedure rdb$db_key real record_version
    recreate recursive references release returning_values returns revoke right rollback row_count rows
    savepoint second select sensitive set similar smallint some sqlcode sqlstate start sum table then
    time timestamp to trailing trigger trim true union unique unknown update updating upper user using
    value values varchar variable varying view when where while with year
'''.split())


class FirebirdDialect:
    """Firebird SQL rendering rules used by source queries."""

    name = 'firebird'

    def serialize_value(self, value) -> str:
        if value is None:
            return 'NULL'
        if isinstance(value, bool):
            return '1' if value else '0'
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    def quote_identifier(self, identifier: str) -> str:
        return '.'.join(self._quote_part(part) for part in identifier.split('.'))

    @staticmethod
    def _quote_part(identifier: str) -> str:
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_$]*', identifier):
            if identifier.lower() not in RESERVED_WORDS:
                return identifier
            return f'"{identifier.upper()}"'
        return '"' + identifier.replace('"', '""') + '"'
