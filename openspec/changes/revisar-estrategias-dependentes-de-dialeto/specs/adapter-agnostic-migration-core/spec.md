## ADDED Requirements

### Requirement: Field strategies return Python values
Core field strategies SHALL produce Python values through `raw_value(row)` and SHALL NOT render SQL. Table and multi-target migrations SHALL serialize each raw value through the configured target adapter and SHALL require `select_columns`, `insert_col` and `raw_value` on every field strategy. The strategies module SHALL NOT expose a serialization helper bound to a fixed dialect.

#### Scenario: Cleaned value is serialized by the target adapter
- **WHEN** a `TableMigration` with a `CpfClean('f_cpf')` field processes the source value `123.456.789-00` with a PostgreSQL target adapter
- **THEN** the insert value is `'12345678900'`
- **AND** the literal is produced by the target adapter

#### Scenario: No serialization on strategies
- **WHEN** migration code looks for `sql_value` in `core.strategies` or for `value()` or `render()` on a core field strategy
- **THEN** none of them is available

#### Scenario: Strategy without raw_value
- **WHEN** a `TableMigration` or `MultiTargetMigration` receives a field object that implements `value()` or `render()` but not `raw_value()`
- **THEN** the migration raises a `TypeError` naming the missing `raw_value` attribute at construction

#### Scenario: Explicit target SQL
- **WHEN** a field is `Fixed('f_created_at', SqlLiteral('CURRENT_TIMESTAMP'))`
- **THEN** the insert value is `CURRENT_TIMESTAMP` without quotes

### Requirement: Regex cleaning keeps what its pattern keeps
`RegexClean` and the strategies derived from it SHALL return the result of applying the pattern and replacement to the source text, without removing other characters, and SHALL accept a `v2_name` for the target column.

#### Scenario: Backslash kept by the pattern
- **WHEN** `RegexClean('f_path', r'\s+', ' ')` reads the source value `C:\docs  historico`
- **THEN** the raw value is `C:\docs historico`
- **AND** a PostgreSQL target stores the backslash unchanged

#### Scenario: Null source value
- **WHEN** a `RegexClean` field reads a null source value
- **THEN** the raw value is `None`

#### Scenario: Renamed target column
- **WHEN** a field is `RegexClean('f_doc_v1', r'[^\d]', v2_name='f_doc')`
- **THEN** the field selects `f_doc_v1` and writes to `f_doc`

### Requirement: Source SQL expressions declare their dialect
A field strategy that injects SQL into the source query SHALL declare the source dialect it was written for, and table, expand and multi-target migrations SHALL verify that dialect against the source adapter before running any source query. Source adapters and dialects SHALL expose a dialect name.

#### Scenario: Matching source dialect
- **WHEN** a `TableMigration` with `SourceSqlExpression("NULLIF(f_cpf, '')", 'f_cpf', dialect='postgres')` runs on an engine whose source adapter dialect name is `postgres`
- **THEN** the source query selects `NULLIF(f_cpf, '') AS f_cpf`

#### Scenario: Different source dialect
- **WHEN** the same migration runs on an engine whose source adapter dialect name is `firebird`
- **THEN** the migration raises a `ValueError` naming the column, the expected dialect `postgres` and the found dialect `firebird`
- **AND** no query is sent to the source, including the progress count

#### Scenario: Dialect is required
- **WHEN** code creates a `SourceSqlExpression` without the `dialect` argument
- **THEN** the construction raises a `TypeError`

#### Scenario: Legacy name removed
- **WHEN** migration code imports `SqlExpression` from `core.strategies`
- **THEN** the import fails

### Requirement: Portable strategies cover common source expressions
The core SHALL provide `NullIfBlank`, `Truncate`, `Upper`, `Lower` and `Default` strategies that select only the source column and apply their rule in Python, so the same migration runs on any source adapter.

#### Scenario: Blank text becomes null
- **WHEN** `NullIfBlank('f_cpf')` reads `''`, `'   '` or `None`
- **THEN** the raw value is `None`
- **AND** for `' 123 '` the raw value is `' 123 '`

#### Scenario: Text is truncated by characters
- **WHEN** `Truncate('f_name', 3)` reads `'Ãngela'`
- **THEN** the raw value is `'Ãng'`
- **AND** a null source value stays `None`

#### Scenario: Case conversion
- **WHEN** `Upper('f_code')` reads `'sp'` and `Lower('f_email')` reads `'Ana@Exemplo.com'`
- **THEN** the raw values are `'SP'` and `'ana@exemplo.com'`

#### Scenario: Default replaces null
- **WHEN** `Default('f_type', 1)` reads `None` and then `3`
- **THEN** the raw values are `1` and `3`

#### Scenario: Same migration on both sources
- **WHEN** a `TableMigration` that uses only portable strategies runs on a PostgreSQL source and on a Firebird source
- **THEN** both source queries select plain column names
- **AND** the generated inserts are equal for equal source rows

### Requirement: Date conversion returns date values
`DateConvert` SHALL return a `datetime.date`, or a `datetime.datetime` when created with `as_datetime=True`, and SHALL return `None` for empty or invalid source values. The rendering of the date SHALL be left to the target adapter.

#### Scenario: Valid date
- **WHEN** `DateConvert('f_birth')` reads `'06/06/2026'`
- **THEN** the raw value is `datetime.date(2026, 6, 6)`
- **AND** a PostgreSQL target adapter renders `'2026-06-06'`

#### Scenario: Date with time
- **WHEN** `DateConvert('f_created', from_fmt='%d/%m/%Y %H:%M', as_datetime=True)` reads `'06/06/2026 10:30'`
- **THEN** the raw value is `datetime.datetime(2026, 6, 6, 10, 30)`
- **AND** a PostgreSQL target adapter renders `'2026-06-06 10:30:00'`

#### Scenario: Invalid date
- **WHEN** `DateConvert('f_birth')` reads `'31/02/2001'`, `''` or `None`
- **THEN** the raw value is `None`
