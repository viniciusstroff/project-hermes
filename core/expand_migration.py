from core.strategies import Fixed
from core.table_migration import TableMigration


class ExpandMigration(TableMigration):
    """Uma linha V1 -> N linhas V2, uma para cada valor em expand_values."""

    def __init__(
        self,
        source_sql: str,
        target: str,
        fields: list,
        expand_col: str,
        expand_values,
        first_fields: list = None,
    ):
        super().__init__(source_sql, target, fields)
        self.expand_col = expand_col
        self.expand_values = list(expand_values)
        self.first_fields = first_fields

    def source_select_columns(self):
        select_cols = []
        active_fields = self.fields if self.first_fields is None else [*self.first_fields, *self.fields]
        for field in active_fields:
            for col in field.select_columns:
                if col not in select_cols:
                    select_cols.append(col)
        return select_cols

    def _expanded_fields(self, value, base_fields):
        return [*base_fields, Fixed(self.expand_col, value)]

    def run(self, engine):
        sql = self.build_source_sql(engine)
        base_total = self.total_rows(engine)
        total = None if base_total is None else base_total * len(self.expand_values)
        engine.report_progress(self.target, 0, total)

        cur = engine.v1_cursor()
        cur.execute(sql)

        processed = 0
        for row in cur:
            for index, value in enumerate(self.expand_values):
                base_fields = self.first_fields if index == 0 and self.first_fields is not None else self.fields
                self.run_row(row, engine, self._expanded_fields(value, base_fields))
                processed += 1
                if processed % engine.progress_every == 0:
                    engine.report_progress(self.target, processed, total)

        engine.report_progress(self.target, processed, total, done=True)
