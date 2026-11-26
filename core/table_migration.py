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

    def run(self, engine: MigrationEngine):
        with engine.unit(self.target) as unit:
            query = self.source_query()
            total = engine.total_rows(query)
            engine.report_progress(self.target, 0, total)
            # Python values: the target adapter serializes them when it renders the Insert.
            values = engine.field_values(self.target, self.fields)

            processed = 0
            for row in engine.read(query):
                unit.row_read()
                engine.insert(self.target, values(row))
                processed += 1
                if processed % engine.progress_every == 0:
                    engine.report_progress(self.target, processed, total)

            engine.report_progress(self.target, processed, total, done=True)
