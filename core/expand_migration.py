from core.strategies import Fixed
from core.table_migration import TableMigration


class ExpandMigration(TableMigration):
    """Uma linha V1 -> N linhas V2, uma para cada valor em expand_values."""

    def __init__(
        self,
        source,
        target: str,
        fields: list,
        expand_col: str,
        expand_values,
        first_fields: list = None,
    ):
        super().__init__(source, target, fields)
        self._validate_fields(first_fields or [])
        self.expand_col = expand_col
        self.expand_values = list(expand_values)
        self.first_fields = first_fields

    def source_fields(self):
        if self.first_fields is None:
            return list(self.fields)
        return [*self.first_fields, *self.fields]

    def _expanded_fields(self, value, base_fields):
        return [*base_fields, Fixed(self.expand_col, value)]

    def run(self, engine):
        with engine.unit(self.target) as unit:
            query = self.source_query()
            base_total = engine.total_rows(query)
            total = None if base_total is None else base_total * len(self.expand_values)
            engine.report_progress(self.target, 0, total)
            # One set of fields per expanded value, built once for the run.
            expansions = []
            for index, value in enumerate(self.expand_values):
                base_fields = self.first_fields if index == 0 and self.first_fields is not None else self.fields
                expansions.append(engine.field_values(self.target, self._expanded_fields(value, base_fields)))

            processed = 0
            for row in engine.read(query):
                # The unit counts source rows; the progress counts expanded rows.
                unit.row_read()
                for values in expansions:
                    engine.insert(self.target, values(row))
                    processed += 1
                    if processed % engine.progress_every == 0:
                        engine.report_progress(self.target, processed, total)

            engine.report_progress(self.target, processed, total, done=True)
