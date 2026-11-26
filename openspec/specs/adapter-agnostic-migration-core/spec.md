# adapter-agnostic-migration-core Specification

## Purpose
Define how the migration core reads source data through neutral queries and expresses target changes as operations that the target adapter renders, without depending on a database driver, query language or server version.
## Requirements
### Requirement: Core consumes source adapters
The migration core SHALL read source data only through the `read(query)` and `count(query)` methods of a `SourceAdapter`, with a `SourceQuery` or a `NativeQuery`, and SHALL NOT import database drivers or call driver APIs.

#### Scenario: Table migration reads through a neutral query
- **WHEN** a `TableMigration` runs with `source=SourceQuery('tb_aluno')`
- **THEN** the engine obtains rows from the `read()` of the configured source adapter
- **AND** the requested fields come from the strategies of the migration

#### Scenario: Same migration on two source databases
- **WHEN** the same `TableMigration` runs once on a PostgreSQL source and once on a MongoDB source holding the same data
- **THEN** both runs emit the same write operations

#### Scenario: Core loads without drivers
- **WHEN** the core modules are imported in an environment without `psycopg2`, `firebirdsql` and `pymongo`
- **THEN** the import succeeds

### Requirement: Source adapters own source-specific query behavior
The source adapter SHALL translate a `SourceQuery`, including its integer limit, into its own query language, and SHALL refuse a `NativeQuery` or a `NativeField` declared for another adapter before reading anything.

#### Scenario: Debug limit
- **WHEN** debug mode sets a limit of 100
- **THEN** the context applies the limit to every query as an integer
- **AND** each adapter renders it in its own syntax

#### Scenario: Native query for another adapter
- **WHEN** a `NativeQuery` declared for `postgres` runs on a `mongodb` source
- **THEN** the migration fails before reading
- **AND** the error names both adapters

#### Scenario: Nested field on a relational source
- **WHEN** a `SourceQuery` asks a `postgres` source for the field `endereco.cidade`
- **THEN** the adapter refuses the query

### Requirement: Core consumes target adapters
Migrations SHALL express target writes as operations (`Insert`, `Clear`, `SuspendConstraints`, `ResumeConstraints`, `ResetIdentity`, `Native` and `Comment`), and the target adapter SHALL translate each operation into zero or more commands of its artifact format.

#### Scenario: Table migration emits inserts
- **WHEN** a `TableMigration` processes a source row
- **THEN** it emits one `Insert` with the target entity and the field values
- **AND** it does not build SQL text

#### Scenario: Multi-target migration emits inserts
- **WHEN** a `MultiTargetMigration` processes a source row with multiple eligible targets
- **THEN** it emits one `Insert` per eligible target

#### Scenario: Operation without meaning in the target
- **WHEN** a `ResetIdentity` operation is rendered by the `mongodb` adapter
- **THEN** the adapter returns no command

#### Scenario: Native operation for another adapter
- **WHEN** a `Native` operation declared for `postgres` reaches the `mongodb` adapter
- **THEN** generation fails with an error naming both adapters

### Requirement: Target dialect owns value serialization
The target adapter, through its dialect when it has one, SHALL serialize Python values into its own format and SHALL refuse a value it cannot represent, naming the entity, the field and the Python type.

#### Scenario: Strategy returns string with quotes
- **WHEN** a field strategy produces a Python string containing a single quote
- **THEN** the PostgreSQL adapter doubles the quote in the SQL literal
- **AND** the MongoDB adapter writes the string unchanged in the JSON document

#### Scenario: Strategy returns null value
- **WHEN** a field strategy produces a null Python value
- **THEN** each target adapter writes its own null representation

### Requirement: Base migration exposes adapter-based context
`BaseMigration` SHALL build its sources and its target from the named connections of the client `.env` through the adapter registry, and SHALL expose each source by its name.

#### Scenario: Migration requests a named source
- **WHEN** a migration calls `self.context.source('v1')`
- **THEN** it receives a migration engine backed by the source adapter declared as `SOURCE_V1_ADAPTER` and by the configured target adapter

#### Scenario: Client without Firebird
- **WHEN** the `.env` declares no Firebird connection
- **THEN** no Firebird connection is attempted and the `firebirdsql` driver is not imported

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

### Requirement: Core exposes no compatibility leftovers
The core package SHALL NOT keep modules or `BaseMigration` helpers whose only role is to forward to the current API, and migration code SHALL obtain source engines only through `context.source(name)`.

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
- **AND** `self.context.source('v1')` returns the engine for the source declared as `SOURCE_V1_ADAPTER`

#### Scenario: Context has no engine by fixed name
- **WHEN** migration code calls `context.engine('pg_v1')`
- **THEN** the call fails because the context exposes sources only through `source(name)`

#### Scenario: Facade writes only operations
- **WHEN** migration code looks for `write_sql()`, `print_comment()`, `sql_value()`, `_open_sql_file()` or `_flush_writes()` on a `BaseMigration`
- **THEN** none of them exists
- **AND** `emit()`, `comment()`, `clear()`, `suspend_constraints()` and `reset_identity()` deliver operations through `context.emit()`

#### Scenario: Database drivers are imported where connections open
- **WHEN** the default context opens the source and target connections
- **THEN** each adapter imports its driver inside its `connect`
- **AND** neither `core.context` nor `core.base_migration` imports `psycopg2` or `firebirdsql`

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

### Requirement: Target maintenance is expressed as operations
The core SHALL offer `Clear`, `SuspendConstraints`, `ResumeConstraints` and `ResetIdentity` as operations with facade shortcuts in `BaseMigration`, and the PostgreSQL target adapter SHALL find the sequence of a column with `pg_get_serial_sequence` instead of a naming convention.

#### Scenario: Reset identity on any column name
- **WHEN** `ResetIdentity('people', 'id')` is rendered by the PostgreSQL adapter
- **THEN** the command uses `pg_get_serial_sequence('people', 'id')`
- **AND** it does not assume a column named `f_id` nor a sequence named `people_id_seq`

#### Scenario: Suspend constraints on PostgreSQL
- **WHEN** `SuspendConstraints()` is rendered by the PostgreSQL adapter
- **THEN** it produces one `ALTER TABLE ... DISABLE TRIGGER ALL` per user table read from the target catalog
- **AND** the table list is read before target reads close, so rendering in later phases does not query the target

#### Scenario: Suspended constraints are resumed by the runner
- **WHEN** a migration emits `SuspendConstraints(['public.a'])` in the `prepare_target` phase
- **THEN** the runner emits `ResumeConstraints(['public.a'])` in the `finalize_target` phase without a call from the migration

#### Scenario: Index helper removed
- **WHEN** migration code looks for `set_index_commands_for` or `set_indexes_commands` on a `BaseMigration`
- **THEN** neither exists and no index command is generated

### Requirement: Calculated source fields are bound to an adapter
A field computed at the source SHALL be declared as `NativeField(adapter, expression, alias)`, which is also a field strategy, and the source adapter SHALL refuse a `NativeField` of another adapter before sending any query, including the progress count.

#### Scenario: Matching adapter
- **WHEN** a `TableMigration` with `NativeField('postgres', "NULLIF(f_cpf, '')", 'f_cpf')` runs on a `postgres` source
- **THEN** the source query selects `NULLIF(f_cpf, '') AS f_cpf`
- **AND** the value read under `f_cpf` goes to the target column `f_cpf`

#### Scenario: Other adapter
- **WHEN** the same migration runs on a `firebird` source
- **THEN** the migration raises a `ValueError` naming the field and both adapters
- **AND** no query is sent to the source

#### Scenario: Adapter is required
- **WHEN** code creates a `NativeField` without the adapter
- **THEN** the construction raises a `TypeError`

#### Scenario: Dialect-bound expression removed
- **WHEN** migration code imports `SourceSqlExpression` or `check_source_dialect` from `core.strategies`
- **THEN** the import fails

### Requirement: Source identifiers are quoted by the adapter
A relational source adapter SHALL quote the entity and field names of a `SourceQuery` when its database would change or misread them unquoted, reserved words included.

#### Scenario: Reserved word as column name
- **WHEN** a `SourceQuery` asks a `postgres` source for the field `user`
- **THEN** the rendered SQL selects `"user"` and not the session user

#### Scenario: Reserved word on Firebird
- **WHEN** a `SourceQuery` asks a `firebird` source for the field `user`
- **THEN** the rendered SQL selects `"USER"`, the name Firebird stores for an unquoted column

