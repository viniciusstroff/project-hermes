# reference-maps Specification

## Purpose
Define how migrations declare the reference maps read from the target, how the runner loads them before the first phase and closes target reads, and how loaded maps keep their key normalization for lookups.
## Requirements
### Requirement: Reference maps are declared by the migration
A migration SHALL declare the reference maps it reads from the target by returning `ReferenceMap` declarations from a `declare_reference_maps()` hook. A declaration SHALL name either a target table with key and value columns, optionally filtered by equality, or a target query that returns the key and the value, but not both.

#### Scenario: Table-based declaration
- **WHEN** a migration declares `ReferenceMap('states_by_code', table='public.states', key='f_code', value='f_id', filters={'f_active': True})`
- **THEN** the metadata provider reads the `f_code` and `f_id` columns of `public.states` restricted to rows where `f_active` equals `true`
- **AND** the migration code contains no SQL for that map

#### Scenario: Query-based declaration
- **WHEN** a migration declares a reference map with `query` set to a target SQL statement
- **THEN** the metadata provider runs that statement and uses its first column as the key and its second column as the value

#### Scenario: Declaration with both table and query
- **WHEN** a declaration sets both `table` and `query`, or neither of them
- **THEN** the run fails before the `prepare_target` phase opens with an error naming the map

#### Scenario: Two declarations with the same name
- **WHEN** `declare_reference_maps()` returns two declarations with the same name
- **THEN** the run fails before the `prepare_target` phase opens with an error naming the repeated map

### Requirement: Declared maps load before the first phase
The migration runner SHALL load every declared reference map exactly once after generating the trigger commands and before opening the `prepare_target` phase, and SHALL log the name and key count of each loaded map.

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
After loading the declared reference maps, the migration context SHALL close target reads, so that no core component queries the target database during the remaining phases.

#### Scenario: Undeclared map requested
- **WHEN** migration code calls `reference_map('unknown')` for a name that was not declared
- **THEN** the context raises an error stating that the map was not declared in `declare_reference_maps()`

#### Scenario: Provider read after close
- **WHEN** any metadata provider method that queries the target is called after target reads are closed
- **THEN** it raises an error instead of querying the target

#### Scenario: Read transaction ends at close
- **WHEN** target reads are closed after the provider has queried the target
- **THEN** the provider connection has no open transaction
- **AND** it holds no locks on target tables

#### Scenario: Cached trigger commands after close
- **WHEN** the trigger enable commands are requested in the `finalize_target` phase
- **THEN** the provider returns the commands generated before target reads were closed without querying the target

### Requirement: Loaded maps keep their key normalization
A loaded reference map SHALL be a read-only mapping that exposes its name and the key normalizer used to build it, and the `Lookup` strategy SHALL use that normalizer when it receives a loaded map without an explicit `key_normalizer`.

#### Scenario: Default normalization
- **WHEN** a declared map without `key_normalizer` loads the key `' SP '`
- **THEN** the loaded map contains the key `'SP'`

#### Scenario: Null keys are skipped
- **WHEN** a declared map with the default `skip_null_keys` reads a row whose key is null
- **THEN** the loaded map has no entry for that row

#### Scenario: Lookup reuses the map normalizer
- **WHEN** a map is declared with `key_normalizer` that upper-cases the key
- **AND** a `Lookup` receives that loaded map without `key_normalizer`
- **THEN** the source value `' sp '` resolves to the value stored for `'SP'`

#### Scenario: Explicit Lookup normalizer wins
- **WHEN** a `Lookup` receives a loaded map and an explicit `key_normalizer`
- **THEN** the `Lookup` normalizes the source value with the explicit function

#### Scenario: Loaded map is read-only
- **WHEN** migration code tries to assign a key in a loaded map
- **THEN** the assignment raises an error

### Requirement: Ambiguous keys are rejected
Loading a reference map SHALL fail when two rows produce the same normalized key with different values, and SHALL accept rows that repeat a key with the same value.

#### Scenario: Same key with different values
- **WHEN** a map normalizes the keys `'sp'` and `'SP '` to `'SP'` and the rows carry the values `25` and `31`
- **THEN** the load fails with an error naming the map, the key `'SP'` and both values

#### Scenario: Same key with the same value
- **WHEN** two rows produce the same normalized key with the same value
- **THEN** the loaded map contains that key once with that value

### Requirement: Ad hoc reference loading is not exposed
The migration context, the `BaseMigration` facade and the metadata provider contract SHALL NOT expose a method that loads a reference map from free SQL at an arbitrary moment of the run.

#### Scenario: Legacy loader removed
- **WHEN** migration code looks for `load_reference_map` on the context, on `BaseMigration` or on the metadata provider
- **THEN** none of them exposes that method
- **AND** `BaseMigration` does not expose a `_reference_maps` attribute

