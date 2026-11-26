from dataclasses import dataclass

from core.queries import check_source, with_default_fields
from core.strategies import validate_field_strategies


@dataclass
class TargetMigration:
    target: str
    fields: list
    condition: callable = None

    def should_write(self, row: dict) -> bool:
        return True if self.condition is None else self.condition(row)


class MultiTargetMigration:
    """Uma linha V1 -> N inserts em targets diferentes.

    ``source`` é um ``SourceQuery`` ou um ``NativeQuery``. Um ``SourceQuery``
    sem campos lê os campos que as estratégias de todos os targets pedem.
    """

    def __init__(self, source, targets: list, label: str = None):
        self.source = check_source(source)
        self.targets = targets
        self.label = label or (targets[0].target if targets else 'multi_target')
        self._validate_targets()

    def _validate_targets(self):
        for target in self.targets:
            if not target.target:
                raise TypeError(f'Invalid target migration {target!r}: target is required')
            validate_field_strategies(target.fields)

    def source_select_columns(self):
        select_cols = []
        for target in self.targets:
            for field in target.fields:
                for col in field.select_columns:
                    if col not in select_cols:
                        select_cols.append(col)
        return select_cols

    def source_query(self):
        return with_default_fields(self.source, self.source_select_columns())

    def run(self, engine):
        with engine.unit(self.label) as unit:
            query = self.source_query()
            total = engine.total_rows(query)
            engine.report_progress(self.label, 0, total)
            targets = [(target, engine.field_values(target.target, target.fields)) for target in self.targets]

            processed = 0
            for row in engine.read(query):
                unit.row_read()
                for target, values in targets:
                    if target.should_write(row):
                        engine.insert(target.target, values(row))
                processed += 1
                if processed % engine.progress_every == 0:
                    engine.report_progress(self.label, processed, total)

            engine.report_progress(self.label, processed, total, done=True)
