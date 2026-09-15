from dataclasses import dataclass

from core.sql import SqlLiteral


@dataclass
class TargetMigration:
    target: str
    fields: list
    condition: callable = None

    def should_write(self, row: dict) -> bool:
        return True if self.condition is None else self.condition(row)


class MultiTargetMigration:
    """Uma linha V1 -> N inserts em targets diferentes."""

    def __init__(self, source_sql: str, targets: list, label: str = None):
        self.source_sql = source_sql
        self.targets = targets
        self.label = label or (targets[0].target if targets else 'multi_target')
        self._validate_targets()

    def _validate_targets(self):
        for target in self.targets:
            if not target.target:
                raise TypeError(f'Invalid target migration {target!r}: target is required')
            for field in target.fields:
                missing = [
                    attr for attr in ('select_columns', 'insert_col')
                    if not hasattr(field, attr)
                ]
                if not hasattr(field, 'render') and not hasattr(field, 'value'):
                    missing.append('render/value')
                if missing:
                    raise TypeError(
                        f'Invalid field strategy {field!r}: missing {", ".join(missing)}'
                    )

    def source_select_columns(self):
        select_cols = []
        for target in self.targets:
            for field in target.fields:
                for col in field.select_columns:
                    if col not in select_cols:
                        select_cols.append(col)
        return select_cols

    def build_source_sql(self, engine):
        select_cols = self.source_select_columns()
        return self.source_sql.format(
            fields=', '.join(select_cols) if select_cols else '*',
            limit=engine.limit_clause,
        )

    def count_sql(self, engine):
        return f'SELECT COUNT(*) FROM ({self.build_source_sql(engine)}) progress_src'

    def total_rows(self, engine):
        try:
            total = engine.scalar(self.count_sql(engine))
        except Exception:
            return None
        return int(total) if total is not None else 0

    def insert_sql(self, row: dict, target: TargetMigration, engine):
        insert_cols = [field.insert_col for field in target.fields]
        insert_vals = [self._field_value(field, row, engine) for field in target.fields]
        return engine.insert_statement(target.target, insert_cols, insert_vals)

    @staticmethod
    def _field_value(field, row: dict, engine):
        if hasattr(field, 'render'):
            return field.render(row, engine.target_adapter)
        return SqlLiteral(field.value(row))

    def run_row(self, row: dict, engine):
        for target in self.targets:
            if not target.should_write(row):
                continue
            engine.write_sql(self.insert_sql(row, target, engine))

    def run(self, engine):
        sql = self.build_source_sql(engine)
        total = self.total_rows(engine)
        engine.report_progress(self.label, 0, total)

        cur = engine.v1_cursor()
        cur.execute(sql)

        processed = 0
        for row in cur:
            self.run_row(row, engine)
            processed += 1
            if processed % engine.progress_every == 0:
                engine.report_progress(self.label, processed, total)

        engine.report_progress(self.label, processed, total, done=True)
