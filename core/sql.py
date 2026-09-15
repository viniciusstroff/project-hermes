from dataclasses import dataclass


@dataclass(frozen=True)
class SqlLiteral:
    """SQL fragment that has already been rendered for the target dialect."""

    sql: str

    def __str__(self) -> str:
        return self.sql
