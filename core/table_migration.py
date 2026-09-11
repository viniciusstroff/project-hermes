from core.migration_engine import MigrationEngine

class TableMigration:
    """Declaração de uma migração de tabela — descreve O QUÊ, não O COMO."""

    def __init__(self, source_sql: str, target: str, fields: list):
        self.source_sql = source_sql
        self.target = target
        self.fields = fields
        self._validate_fields(fields)

    @staticmethod
    def _validate_fields(fields: list):
        for field in fields:
            missing = [
                attr for attr in ('select_columns', 'insert_col', 'value')
                if not hasattr(field, attr)
            ]
            if missing:
                raise TypeError(
                    f'Invalid field strategy {field!r}: missing {", ".join(missing)}'
                )

    def source_select_columns(self):
        select_cols = []
        for field in self.fields:
            for col in field.select_columns:
                if col not in select_cols:
                    select_cols.append(col)
        return select_cols

    def build_source_sql(self, engine: MigrationEngine):
        select_cols = self.source_select_columns()
        return self.source_sql.format(
            fields=', '.join(select_cols) if select_cols else '*',
            limit=engine.limit_clause,
        )

    def count_sql(self, engine: MigrationEngine):
        return f'SELECT COUNT(*) FROM ({self.build_source_sql(engine)}) progress_src'

    def total_rows(self, engine: MigrationEngine):
        try:
            total = engine.scalar(self.count_sql(engine))
        except Exception:
            return None
        return int(total) if total is not None else 0

    def insert_sql(self, row: dict, fields: list = None):
        active_fields = fields or self.fields
        insert_cols = [field.insert_col for field in active_fields]
        insert_vals = [field.value(row) for field in active_fields]
        return 'INSERT INTO {table} ({cols}) VALUES ({vals});\n'.format(
            table=self.target,
            cols=', '.join(insert_cols),
            vals=', '.join(insert_vals),
        )

    def run_row(self, row: dict, engine: MigrationEngine, fields: list = None):
        engine.write_sql(self.insert_sql(row, fields))

    def run(self, engine: MigrationEngine):
        sql = self.build_source_sql(engine)
        total = self.total_rows(engine)
        engine.report_progress(self.target, 0, total)

        cur = engine.v1_cursor()
        cur.execute(sql)

        processed = 0
        for row in cur:
            self.run_row(row, engine)
            processed += 1
            if processed % engine.progress_every == 0:
                engine.report_progress(self.target, processed, total)

        engine.report_progress(self.target, processed, total, done=True)
