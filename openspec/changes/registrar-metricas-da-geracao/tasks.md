## 1. Metrics Model

- [x] 1.1 Create `core/metrics.py` with `GenerationMetrics`, the unit and phase records and `LostValue(value, reason, sample, reference_map=None)`.
- [x] 1.2 Keep unit counters as plain integers: rows read, inserts per target entity, start, end, duration and state (`done` or `interrupted`), with a stack of open units.
- [x] 1.3 Group losses by unit, target entity, column, strategy and reason, keeping the count and at most 20 distinct samples truncated to 100 characters.
- [x] 1.4 Write `relatorio-geracao.json` at the run root with run id, status, error, debug limit, timestamps, phases, native copies, units, losses and the size of each artifact listed in the manifest.

## 2. Context, Engine and Runner

- [x] 2.1 Create the collector in `DefaultMigrationContextFactory`, expose it as `context.metrics` and add `context.open_phase()`, which opens the phase in the output and in the collector.
- [x] 2.2 Pass the collector and the source name to `MigrationEngine` in `context.source(name)`, and add `engine.unit(label)` and `engine.field_values(entity, fields)`. Count each `engine.insert()` in the open unit.
- [x] 2.3 Open every phase through the context, in the factory and in the runner.
- [x] 2.4 Write the generation report after `finish(True)` and before the `success` manifest, and in the error path after `finish(False)` and before the `failed` manifest, closing the open units as `interrupted`.
- [x] 2.5 Record each native copy with its source, tool, entities, duration and file size.
- [x] 2.6 Make `FileOutput.finish()` flush the open file, so the report reads the final artifact sizes.

## 3. Migrations and Strategies

- [x] 3.1 Add `FieldStrategy.resolve(row)` returning `raw_value(row)`.
- [x] 3.2 Implement `resolve` in `Lookup` (`lookup_miss`, with the map name when available), `DateConvert` (`invalid_date`) and `EmailExtract` (`email_not_found`), treating null and empty source values as no loss.
- [x] 3.3 In `TableMigration`, `ExpandMigration`, `MultiTargetMigration` and `CopyEntity`, run inside `engine.unit()`, count rows read and take field values through `engine.field_values()`.

## 4. Logging

- [x] 4.1 Add an optional `level` argument to `MigrationLogger.print_log()`, passing `error` where the core logs `[ERRO]` and `warning` in `warn_once()`.
- [x] 4.2 Write `logs/migration.jsonl` with timestamp, run id, level, phase, unit, event, message and data, keeping the `migration.log` line format.
- [x] 4.3 Emit `phase_started`, `phase_finished`, `unit_started`, `unit_finished` and `data_loss` events from the collector.
- [x] 4.4 Log the end-of-run summary: one line per unit and one warning per loss group with up to five samples.

## 5. Documentation

- [x] 5.1 Document `relatorio-geracao.json`, `migration.jsonl` and the end-of-run summary in `docs/execucao.md`.
- [x] 5.2 Document the collector, `engine.unit()`, `engine.field_values()` and `LostValue` in `docs/arquitetura.md`.
- [x] 5.3 Document `resolve` and `LostValue` for client strategies in `docs/estrategias.md`.
- [x] 5.4 Update item 14 of `docs/melhorias-futuras.md` with what was implemented and what remains.

## 6. Verification

- [x] 6.1 Add collector tests: unit counters, phase totals, native copies, loss grouping, sample limits and report content for success and failure.
- [x] 6.2 Add migration tests with fake sources for `TableMigration`, `ExpandMigration` (rows read versus inserts), `MultiTargetMigration` (conditional target) and `CopyEntity`, including an interrupted unit and an insert outside a unit.
- [x] 6.3 Add strategy tests for `resolve` in `Lookup`, `DateConvert` and `EmailExtract`, including null and empty values and a client strategy without `resolve`.
- [x] 6.4 Add logger tests for levels, the unchanged text format and the JSON Lines events.
- [x] 6.5 Check that `manifest.json` and the artifacts of a generated run are identical before and after this change.
- [x] 6.6 Run the full `AcmeEdu` generation, compare the report counts with `conferir.py` and record the generation time before and after this change.
- [x] 6.7 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core AcmeEdu tests`.
- [x] 6.8 Run `openspec validate registrar-metricas-da-geracao --strict`.
