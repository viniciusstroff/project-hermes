## 1. Metrics Model

- [ ] 1.1 Create `core/metrics.py` with `GenerationMetrics`, the unit and phase records and `LostValue(value, reason, sample, reference_map=None)`.
- [ ] 1.2 Keep unit counters as plain integers: rows read, statements per target table, start, end, duration and state (`done` or `interrupted`).
- [ ] 1.3 Group losses by unit, target table, column, strategy and reason, keeping the count and at most 20 distinct samples truncated to 100 characters.
- [ ] 1.4 Write `relatorio-geracao.json` at the run root with run id, status, debug limit, timestamps, phases, units, losses and the size of each existing file in `comandos/`.

## 2. Context, Engine and Runner

- [ ] 2.1 Create the collector in `DefaultMigrationContextFactory` and expose it as `context.metrics`.
- [ ] 2.2 Pass the collector and the source name to `MigrationEngine` in `context.source(source_name)`.
- [ ] 2.3 Record each `open_phase` in the runner, closing the previous phase.
- [ ] 2.4 Write the generation report after `finish(True)` and before the `success` manifest, and in the error path before the `failed` manifest, marking the running unit as `interrupted`.
- [ ] 2.5 Record the `copy_compatible_entities` phase in the runner and, for a native copy, its duration, `pg_dump` exit code and file size.

## 3. Migrations and Strategies

- [ ] 3.1 Add `FieldStrategy.resolve(row)` returning `raw_value(row)`.
- [ ] 3.2 Implement `resolve` in `Lookup` (`lookup_miss`, with the map name when available), `DateConvert` (`invalid_date`) and `EmailExtract` (`email_not_found`), treating null and empty source values as no loss.
- [ ] 3.3 In `TableMigration`, `ExpandMigration` and `MultiTargetMigration`, open a unit in `run()`, count rows read and statements per target, call `resolve` when the field defines it, record `LostValue` losses and serialize the unwrapped value through the target adapter.
- [ ] 3.4 Finish the unit as `interrupted` when an exception leaves `run()`.

## 4. Logging

- [ ] 4.1 Add an optional `level` argument to `MigrationLogger.print_log()` and pass `error` where the core logs `[ERRO]`.
- [ ] 4.2 Write `logs/migration.jsonl` with timestamp, run id, level, phase, unit, event, message and data, keeping the `migration.log` line format.
- [ ] 4.3 Emit `phase_started`, `phase_finished`, `unit_started`, `unit_finished` and `data_loss` events from the collector.
- [ ] 4.4 Log the end-of-run summary: one line per unit and one warning per loss group with up to five samples.

## 5. Documentation

- [ ] 5.1 Document `relatorio-geracao.json`, `migration.jsonl` and the end-of-run summary in `docs/execucao.md`.
- [ ] 5.2 Document the collector and `LostValue` in `docs/arquitetura.md`.
- [ ] 5.3 Document `resolve` and `LostValue` for client strategies in `docs/estrategias.md`.
- [ ] 5.4 Update item 14 of `docs/melhorias-futuras.md` with what was implemented and what remains.

## 6. Verification

- [ ] 6.1 Add collector tests: unit counters, phase totals, loss grouping, sample limits and report content for success and failure.
- [ ] 6.2 Add migration tests with fake sources for `TableMigration`, `ExpandMigration` (rows read versus statements) and `MultiTargetMigration` (conditional target), including an interrupted unit.
- [ ] 6.3 Add strategy tests for `resolve` in `Lookup`, `DateConvert` and `EmailExtract`, including null and empty values and a client strategy without `resolve`.
- [ ] 6.4 Add logger tests for levels, the unchanged text format and the JSON Lines events.
- [ ] 6.5 Check that `manifest.json` and the SQL artifacts of a generated run are identical before and after this change.
- [ ] 6.6 Run the full `AcmeEdu` generation, compare the report counts with `conferir.py` and record the generation time before and after this change.
- [ ] 6.7 Run `python3 -m unittest discover -s tests` and `python3 -m compileall core Acme tests`.
- [ ] 6.8 Run `openspec validate registrar-metricas-da-geracao`.
