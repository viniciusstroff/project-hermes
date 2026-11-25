# adapter-agnostic-migration-core Specification

## Purpose
Define how the migration core reads source data, renders target SQL and runs target metadata operations through source adapters, target adapters, dialects and metadata providers, without depending on concrete PostgreSQL or Firebird driver APIs.
## Requirements
### Requirement: Core consumes source adapters
The migration core SHALL read source data through a `SourceAdapter` contract instead of calling concrete PostgreSQL or Firebird connection and cursor APIs directly.

#### Scenario: Engine reads PostgreSQL source through adapter
- **WHEN** a table migration runs against the default PostgreSQL V1 source
- **THEN** the engine obtains rows through the configured source adapter without referencing `psycopg2` cursor APIs in the engine

#### Scenario: Engine reads Firebird source through adapter
- **WHEN** a table migration runs against the default Firebird V1 source
- **THEN** the engine obtains dictionary-like rows through the configured source adapter without branching on a Firebird source flag in the engine

### Requirement: Source adapters own source-specific query behavior
The source adapter or its dialect SHALL own source-specific query behavior, including pagination or debug limit syntax.

#### Scenario: PostgreSQL source applies debug limit
- **WHEN** debug mode sets a limit of 100 for a PostgreSQL source query
- **THEN** the rendered source SQL uses PostgreSQL-compatible limit syntax

#### Scenario: Firebird source applies debug limit
- **WHEN** debug mode sets a limit of 100 for a Firebird source query
- **THEN** the rendered source SQL uses Firebird-compatible row limiting syntax

### Requirement: Core consumes target adapters
The migration core SHALL build target insert statements through a `TargetAdapter` contract instead of formatting PostgreSQL `INSERT` statements directly in migration classes.

#### Scenario: Table migration writes target insert
- **WHEN** a `TableMigration` processes a source row
- **THEN** it passes the target table, insert columns, and field values to the configured target adapter to produce the output statement

#### Scenario: Multi-target migration writes target inserts
- **WHEN** a `MultiTargetMigration` processes a source row with multiple eligible targets
- **THEN** each target insert is produced through the configured target adapter

### Requirement: Target dialect owns value serialization
The target adapter or target dialect SHALL own SQL serialization of Python values used in generated output.

#### Scenario: Strategy returns string with quotes
- **WHEN** a field strategy produces a Python string containing a single quote
- **THEN** the generated target statement escapes the value using the configured target dialect

#### Scenario: Strategy returns null value
- **WHEN** a field strategy produces a null Python value
- **THEN** the generated target statement serializes the value using the configured target dialect null representation

### Requirement: Metadata operations are provider-backed
The migration core SHALL perform auxiliary target metadata operations through a `MetadataProvider` contract instead of embedding PostgreSQL catalog queries in `BaseMigration` or Acme code.

#### Scenario: Trigger commands are generated for PostgreSQL target
- **WHEN** the default PostgreSQL target prepares trigger disable and enable commands
- **THEN** the commands are provided by the PostgreSQL metadata provider

#### Scenario: Reference map is loaded from target
- **WHEN** an Acme declares a reference map in `declare_reference_maps()`
- **THEN** the key and value pairs are read through the configured metadata provider before the `prepare_target` phase opens
- **AND** the loaded map is cached by the migration context under the declared name

### Requirement: Base migration exposes adapter-based context
`BaseMigration` SHALL construct and expose adapter-based source, target, dialect, and metadata context while preserving the current default PostgreSQL V1, Firebird V1, and PostgreSQL V2 configuration path.

#### Scenario: Default migration starts with current environment keys
- **WHEN** a migration is created using the existing `.env` keys for PostgreSQL V1, Firebird V1, and PostgreSQL V2
- **THEN** `BaseMigration` creates the corresponding adapters and the migration can run with behavior equivalent to the current flow

#### Scenario: Acme code requests source engine
- **WHEN** an Acme uses the core helper for the primary source or Firebird source
- **THEN** it receives a migration engine backed by the corresponding source adapter and configured target adapter

### Requirement: Direct driver access is transitional only
The core SHALL provide adapter-first helpers for Acmes so new migration code does not need to access `pg_v1_conn`, `pg_v2_conn`, or `fb_v1_conn` directly.

#### Scenario: Acme model uses adapter helpers
- **WHEN** the model Acme performs table migrations, reference lookups, trigger handling, index handling, or sequence resets
- **THEN** it uses core helpers, adapters, or metadata providers instead of direct driver connection attributes

#### Scenario: Legacy connection attributes are still present during transition
- **WHEN** existing Acme code still depends on legacy connection attributes
- **THEN** the default migration context keeps compatible accessors until those Acmes are migrated

### Requirement: PostgreSQL dialect preserves string content
The PostgreSQL target dialect SHALL serialize Python strings so that the value stored in the target is identical to the original string, including backslashes, regardless of the `standard_conforming_strings` setting of the session that applies the generated SQL.

#### Scenario: String with backslash
- **WHEN** a field value is the string `a\b`
- **THEN** the dialect renders `E'a\\b'`
- **AND** the target stores `a\b`

#### Scenario: String ending with backslash
- **WHEN** a field value ends with a backslash
- **THEN** the rendered literal is closed by its final quote
- **AND** the statement that follows it in the artifact is not absorbed by the literal

#### Scenario: Backslash next to a quote
- **WHEN** a field value is `O\'Neil`
- **THEN** the dialect renders `E'O\\''Neil'`
- **AND** the target stores `O\'Neil`

#### Scenario: Text that looks like an escape sequence
- **WHEN** a field value contains `\n`, `\x41` or `\101` as plain text
- **THEN** the target stores those characters literally, without turning them into a line break or into `A`

#### Scenario: Session with standard_conforming_strings off
- **WHEN** the generated SQL is applied in a session with `standard_conforming_strings = off`
- **THEN** the target stores the same value as in a session with the setting on

#### Scenario: String without backslash
- **WHEN** a field value has no backslash
- **THEN** the dialect renders a standard `'...'` literal with doubled single quotes, unchanged from the previous behavior

### Requirement: Firebird source is optional
The default migration context SHALL connect to the Firebird V1 source only when `FB_V1_HOST` is configured with a non-empty value.

#### Scenario: Firebird not configured
- **WHEN** the client `.env` has no `FB_V1_HOST` or an empty one
- **THEN** the default context opens the PostgreSQL V1 and V2 connections without trying a Firebird connection
- **AND** the context has no `fb_v1` source and the legacy `fb_v1_conn` attribute is `None`

#### Scenario: Engine requested for a missing source
- **WHEN** a client calls `context.engine('fb_v1')` and Firebird is not configured
- **THEN** the context raises an error stating that the `fb_v1` source is not configured

#### Scenario: Firebird configured
- **WHEN** the client `.env` has the `FB_V1_*` keys filled in
- **THEN** the default context connects to Firebird and registers the `fb_v1` source as before

### Requirement: Core exposes no compatibility leftovers
The core package SHALL NOT keep modules or `BaseMigration` helpers whose only role is to forward to the current API, and migration code SHALL obtain source engines only through `context.engine(name)`.

#### Scenario: Legacy engine module removed
- **WHEN** code imports `core.engine`
- **THEN** the import fails because the module does not exist
- **AND** `TableMigration`, `ExpandMigration`, `MultiTargetMigration`, `TargetMigration` and `MigrationEngine` are importable from `core` and from their own modules

#### Scenario: Core exports resolve to current modules
- **WHEN** code reads each name listed in `core.__all__`
- **THEN** every name resolves without importing a compatibility module

#### Scenario: Facade has no private engine or progress helpers
- **WHEN** migration code looks for `_engine`, `_fb_engine`, `_v1_cursor`, `_log_table_progress`, `_setup_tui`, `_teardown_tui`, `_footer_lines`, `_render_progress_footer`, `_reset_progress`, `_finish_progress`, `_log_file` or `_tui_active` on a `BaseMigration`
- **THEN** none of these attributes exists
- **AND** `self.context.engine('pg_v1')` returns the engine for the PostgreSQL V1 source

#### Scenario: Facade keeps its writing helpers
- **WHEN** migration code calls `write_sql()`, `print_comment()`, `_open_sql_file()` or `_flush_writes()` on a `BaseMigration`
- **THEN** the call is delegated to the configured output adapter as before

#### Scenario: Database drivers are imported where connections open
- **WHEN** the default context opens the source and target connections
- **THEN** the drivers are used from `core.context`
- **AND** `core.base_migration` does not import `psycopg2` or `firebirdsql`

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

### Requirement: PostgreSQL dialect serializes binary values
The PostgreSQL target dialect SHALL serialize `bytes`, `bytearray` and `memoryview` values as a hexadecimal `decode` expression that does not depend on the `standard_conforming_strings` setting.

#### Scenario: Bytes value
- **WHEN** a field value is `b'AB'`
- **THEN** the dialect renders `decode('4142', 'hex')`

#### Scenario: Memoryview from a bytea column
- **WHEN** a field value is a `memoryview` over `b'AB'`
- **THEN** the dialect renders `decode('4142', 'hex')`
- **AND** the rendered literal does not contain the Python representation of the object

#### Scenario: Empty binary value
- **WHEN** a field value is `b''`
- **THEN** the dialect renders `decode('', 'hex')`

#### Scenario: Binary value is reported once per column
- **WHEN** a `TableMigration` for `public.documents` renders binary values in the column `f_content` for several rows
- **THEN** the generation log records once that `public.documents.f_content` received binary values

### Requirement: PostgreSQL dialect serializes special numeric values
The PostgreSQL target dialect SHALL render not-a-number and infinite values as typed literals and SHALL render finite `Decimal` values as unquoted numeric constants.

#### Scenario: Float special values
- **WHEN** field values are `float('nan')`, `float('inf')` and `float('-inf')`
- **THEN** the dialect renders `'NaN'::float8`, `'Infinity'::float8` and `'-Infinity'::float8`

#### Scenario: Decimal special values
- **WHEN** field values are `Decimal('NaN')` and `Decimal('Infinity')`
- **THEN** the dialect renders `'NaN'::numeric` and `'Infinity'::numeric`

#### Scenario: Finite Decimal
- **WHEN** a field value is `Decimal('85.00')`
- **THEN** the dialect renders `85.00` without quotes

### Requirement: PostgreSQL dialect renders temporal values in ISO format
The PostgreSQL target dialect SHALL render `date`, `datetime` and `time` values as quoted ISO 8601 text, with a space between date and time and with the UTC offset when the value has a time zone.

#### Scenario: Date value
- **WHEN** a field value is `date(2026, 6, 6)`
- **THEN** the dialect renders `'2026-06-06'`

#### Scenario: Datetime value
- **WHEN** a field value is `datetime(2026, 6, 6, 10, 30)`
- **THEN** the dialect renders `'2026-06-06 10:30:00'`

#### Scenario: Datetime with time zone
- **WHEN** a field value is `datetime(2026, 6, 6, 10, 30)` with a UTC offset of minus three hours
- **THEN** the dialect renders `'2026-06-06 10:30:00-03:00'`

#### Scenario: Time value
- **WHEN** a field value is `time(10, 30)`
- **THEN** the dialect renders `'10:30:00'`

### Requirement: Unsupported values fail during generation
The PostgreSQL target dialect SHALL reject values it cannot represent, including text with a NUL character and any type outside its supported list, and table and multi-target migrations SHALL report the target table, the column and the Python type of the rejected value without including the value itself.

#### Scenario: Text with NUL character
- **WHEN** a `TableMigration` for `public.notes` renders the column `f_text` with a value containing `\x00`
- **THEN** generation fails with an error naming `public.notes`, `f_text` and the position of the NUL character
- **AND** the error message does not contain the value

#### Scenario: Unsupported type
- **WHEN** a field value is a `list`, a `dict` or a `timedelta`
- **THEN** generation fails with an error naming the target table, the column and the Python type

#### Scenario: Run records the failure
- **WHEN** a value is rejected during the `load_transformed_data` phase
- **THEN** the run ends with manifest status `failed` and the error message from the migration

