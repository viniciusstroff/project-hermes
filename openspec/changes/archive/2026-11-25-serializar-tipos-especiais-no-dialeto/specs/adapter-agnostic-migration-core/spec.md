## ADDED Requirements

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
