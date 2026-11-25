from core.migration_engine import MigrationEngine
from core.queries import check_source, with_default_fields
from core.strategies import validate_field_strategies


class TableMigration:
    """Declaração de uma migração de tabela — descreve O QUÊ, não O COMO.

    ``source`` é um ``SourceQuery`` ou um ``NativeQuery``. Um ``SourceQuery``
    sem campos lê os campos que as estratégias pedem.
    """

    def __init__(self, source, target: str, fields: list):
        self.source = check_source(source)
        self.target = target
        self.fields = fields
        self._validate_fields(fields)

    @staticmethod
    def _validate_fields(fields: list):
        validate_field_strategies(fields)

    def source_fields(self):
        """Strategies that contribute to the source query."""
        return list(self.fields)

    def source_select_columns(self):
        select_cols = []
        for field in self.source_fields():
            for col in field.select_columns:
                if col not in select_cols:
                    select_cols.append(col)
        return select_cols

    def source_query(self):
        return with_default_fields(self.source, self.source_select_columns())

    def run_row(self, row: dict, engine: MigrationEngine, fields: list = None):
        # Python values: the target adapter serializes them when it renders the Insert.
        engine.insert(self.target, [(field.insert_col, field.raw_value(row)) for field in fields or self.fields])

    def run(self, engine: MigrationEngine):
        query = self.source_query()
        total = engine.total_rows(query)
        engine.report_progress(self.target, 0, total)

        processed = 0
        for row in engine.read(query):
            self.run_row(row, engine)
            processed += 1
            if processed % engine.progress_every == 0:
                engine.report_progress(self.target, processed, total)

        engine.report_progress(self.target, processed, total, done=True)
