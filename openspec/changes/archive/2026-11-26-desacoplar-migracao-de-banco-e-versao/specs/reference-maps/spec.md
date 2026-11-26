## MODIFIED Requirements

### Requirement: Reference maps are declared by the migration
A migration SHALL declare the reference maps it reads from the target by returning `ReferenceMap` declarations from a `declare_reference_maps()` hook. A declaration SHALL name either a target table with key and value columns, optionally filtered by equality, or a `NativeQuery` of the target adapter that returns the key and the value, but not both. The target adapter SHALL read the pairs through its `read()` method.

#### Scenario: Table-based declaration
- **WHEN** a migration declares `ReferenceMap('states_by_code', table='public.states', key='f_code', value='f_id', filters={'f_active': True})`
- **THEN** the target adapter reads a `SourceQuery` of the `f_code` and `f_id` fields of `public.states` restricted to rows where `f_active` equals `true`
- **AND** the migration code contains no SQL for that map

#### Scenario: Query-based declaration
- **WHEN** a migration declares a reference map with `query=NativeQuery('postgres', sql)`
- **THEN** the target adapter runs that query and the map uses the first field of each row as the key and the second field as the value

#### Scenario: Query as plain text
- **WHEN** a declaration sets `query` to a string instead of a `NativeQuery`
- **THEN** the declaration raises an error naming the map and pointing to `NativeQuery`

#### Scenario: Declaration with both table and query
- **WHEN** a declaration sets both `table` and `query`, or neither of them
- **THEN** the run fails before the `prepare_target` phase opens with an error naming the map

#### Scenario: Two declarations with the same name
- **WHEN** `declare_reference_maps()` returns two declarations with the same name
- **THEN** the run fails before the `prepare_target` phase opens with an error naming the repeated map

### Requirement: Declared maps load before the first phase
The migration runner SHALL load every declared reference map exactly once before opening the `prepare_target` phase, and SHALL log the name and key count of each loaded map.

#### Scenario: Maps load before prepare_target
- **WHEN** a migration with declared reference maps runs
- **THEN** every declared map is loaded before the output opens the `prepare_target` phase
- **AND** the log records each map name with its number of keys

#### Scenario: Empty map
- **WHEN** a declared map loads no rows
- **THEN** the run continues
- **AND** the log records a warning naming the empty map

#### Scenario: Map load fails
- **WHEN** loading a declared map raises an error
- **THEN** the run ends with manifest status `failed` and an error naming the map
- **AND** the output opens no phase after `setup`

#### Scenario: Migration declares no maps
- **WHEN** a migration does not override `declare_reference_maps()`
- **THEN** the run proceeds without loading reference maps

### Requirement: Target reads close after maps load
After loading the declared reference maps, the migration context SHALL close target reads through the `close_reads()` of the target adapter, so that no core component queries the target database during the remaining phases.

#### Scenario: Undeclared map requested
- **WHEN** migration code calls `reference_map('unknown')` for a name that was not declared
- **THEN** the context raises an error stating that the map was not declared in `declare_reference_maps()`

#### Scenario: Target read after close
- **WHEN** the `read()` of the target adapter is called after target reads are closed
- **THEN** it raises an error instead of querying the target

#### Scenario: Read transaction ends at close
- **WHEN** target reads are closed after the target adapter has queried the target
- **THEN** the target connection has no open transaction
- **AND** it holds no locks on target tables

#### Scenario: Catalog read before close
- **WHEN** a `SuspendConstraints` or `ResumeConstraints` without entities is rendered after target reads are closed
- **THEN** the PostgreSQL adapter uses the table list it read before closing, without querying the target

### Requirement: Ad hoc reference loading is not exposed
The migration context, the `BaseMigration` facade and the target adapter contract SHALL NOT expose a method that loads a reference map from free SQL at an arbitrary moment of the run.

#### Scenario: Legacy loader removed
- **WHEN** migration code looks for `load_reference_map` on the context, on `BaseMigration` or on the target adapter
- **THEN** none of them exposes that method
- **AND** `BaseMigration` does not expose a `_reference_maps` attribute
