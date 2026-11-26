# database-adapters Specification

## Purpose
Define how source and target connections are declared by name and resolved to adapters, how adapters load their drivers, check the server version and declare capabilities, and how compatible entities, the optional pg_dump copy and the MongoDB adapter work.
## Requirements
### Requirement: Connections are declared by name
The client configuration SHALL declare each source as a group of keys `SOURCE_<NAME>_*` with a `SOURCE_<NAME>_ADAPTER` key, and the target as `TARGET_ADAPTER` plus `TARGET_*` keys. The source name is the lower-case `<NAME>`, and the remaining suffixes of each group are passed to the adapter as its parameters.

#### Scenario: Two sources of different databases
- **WHEN** the `.env` declares `SOURCE_V1_ADAPTER=postgres` and `SOURCE_LEGADO_ADAPTER=firebird` with their parameters
- **THEN** the context opens both sources
- **AND** `context.source('v1')` and `context.source('legado')` return engines backed by the PostgreSQL and Firebird adapters

#### Scenario: Missing adapter parameter
- **WHEN** a connection group lacks a parameter its adapter requires
- **THEN** the run stops before generation with an error naming the connection and the missing keys

#### Scenario: Unknown source name
- **WHEN** a migration asks for a source that the `.env` does not declare
- **THEN** the error names the requested source and lists the declared ones

### Requirement: Adapters are resolved by a registry and load drivers on demand
The adapter registry SHALL resolve the built-in names `postgres`, `firebird` and `mongodb`, SHALL import any other value in the form `package.module:Class`, and SHALL import a database driver only when an adapter that needs it connects.

#### Scenario: Only the used driver is required
- **WHEN** a client uses only PostgreSQL connections in an environment without `firebirdsql` and `pymongo`
- **THEN** generation and application run normally

#### Scenario: Third-party adapter by path
- **WHEN** the `.env` declares `TARGET_ADAPTER=hermes_extra.cassandra:CassandraTarget`
- **THEN** the registry imports that class and uses it as the target adapter
- **AND** it refuses the class with a clear error if a method of the target contract is missing

#### Scenario: Adapter without the requested role
- **WHEN** the `.env` declares `TARGET_ADAPTER=firebird`
- **THEN** the run stops with an error stating that the `firebird` adapter has no target role

### Requirement: Adapters detect the server version and enforce their supported range
Each adapter SHALL read the server version when it connects, SHALL declare the range it supports for each role, and SHALL stop the run before generation when the server is outside that range. Behavior that differs between versions SHALL stay inside the adapter.

#### Scenario: Supported versions
- **WHEN** the source is PostgreSQL 9.6 or 17 and the target is PostgreSQL 12 or newer
- **THEN** the run proceeds without version-specific configuration

#### Scenario: Unsupported version
- **WHEN** a source server is older than the range its adapter declares
- **THEN** the run stops before generation with an error naming the adapter, the server version and the supported range

#### Scenario: Version recorded in the manifest
- **WHEN** a run starts
- **THEN** `manifest.json` records, for each source and for the target, the adapter name and the server version, without passwords

#### Scenario: Target older than its range
- **WHEN** the target is PostgreSQL 11
- **THEN** the run stops before generation stating that the `postgres` adapter accepts targets from version 12

#### Scenario: Firebird version
- **WHEN** a Firebird source connects
- **THEN** the adapter reads `ENGINE_VERSION` and refuses a server older than 2.5
- **AND** a server that cannot report its version is refused with the connection named in the message

### Requirement: Drivers and client tools are installed per adapter
The core requirements SHALL NOT include database drivers. Each adapter SHALL list its driver in `requirements/<adapter>.txt`, and the Hermes image SHALL install the PostgreSQL client tools only when the `PG_CLIENT_VERSION` build argument names a major version.

#### Scenario: Client with PostgreSQL only
- **WHEN** a client installs `requirements.txt` and `requirements/postgres.txt`
- **THEN** generation and application run without `firebirdsql`

#### Scenario: Image without client tools
- **WHEN** the image is built without `PG_CLIENT_VERSION`
- **THEN** it has the drivers of every adapter and no `pg_dump` nor `psql`
- **AND** a source with `NATIVE_COPY=pg_dump` stops before generation stating that the binary does not run

#### Scenario: Image with a newer client
- **WHEN** the image is built with `PG_CLIENT_VERSION=17` and the source server is PostgreSQL 9.6
- **THEN** the native copy runs, because a newer `pg_dump` exports from an older server

### Requirement: Adapters declare capabilities
Each target adapter SHALL declare whether it supports transactions, savepoints and constraint suspension, and the core SHALL decide what it can do from these capabilities instead of from the adapter name.

#### Scenario: MongoDB capabilities depend on the deployment
- **WHEN** the MongoDB target is a replica set
- **THEN** the adapter declares transactions as supported and savepoints as unsupported
- **AND** when the MongoDB target is a standalone server, it declares transactions as unsupported

### Requirement: Compatible entities are copied through the adapters
The migration SHALL list the entities that pass without transformation in `compatible_entities()` as `CopyEntity(entity, source=..., target=None)`, each one bound to a named source and optionally renamed, and the core SHALL copy them in the `copy_compatible_entities` phase by reading each one from its source adapter and emitting one `Insert` per row.

#### Scenario: Copy between different databases
- **WHEN** `compatible_entities()` returns `holidays` with a PostgreSQL source and a MongoDB target
- **THEN** every `holidays` row is written to the `holidays` collection

#### Scenario: Entity not listed
- **WHEN** a source table exists but is not returned by `compatible_entities()` nor read by a migration
- **THEN** nothing from it reaches the target

#### Scenario: Renamed entity
- **WHEN** `compatible_entities()` returns `CopyEntity('public.cidades_v1', source='v1', target='public.cidades')`
- **THEN** every row of `public.cidades_v1` is written to `public.cidades`

#### Scenario: Debug mode
- **WHEN** debug mode sets a limit of 5
- **THEN** each compatible entity is copied row by row with at most 5 rows, even when a native copy is enabled

### Requirement: PostgreSQL native copy is optional and version-checked
The PostgreSQL source adapter SHALL offer `pg_dump` as an optional native copy for the same entity list when the target is also PostgreSQL, and SHALL refuse to run it when the major version of the `pg_dump` binary is older than the source server.

#### Scenario: Native copy enabled
- **WHEN** `SOURCE_V1_NATIVE_COPY=pg_dump` is set, the target is PostgreSQL and the `pg_dump` binary is as new as the source server
- **THEN** the `copy_compatible_entities` artifact comes from `pg_dump` limited to the listed entities

#### Scenario: Binary older than the server
- **WHEN** native copy is enabled with `pg_dump` 9.6 and the source server is PostgreSQL 17
- **THEN** the run stops before generation with an error naming both versions

#### Scenario: Native copy with another target
- **WHEN** native copy is enabled and the target adapter is not `postgres`
- **THEN** the run stops before generation with an error naming the `NATIVE_COPY` key and the target adapter

#### Scenario: Renamed entity with native copy
- **WHEN** native copy is enabled and one compatible entity of that source is renamed
- **THEN** `pg_dump` copies the entities that keep their name
- **AND** the renamed entity is copied row by row, before the included `pg_dump` output

### Requirement: MongoDB adapter reads and writes documents
The MongoDB adapter SHALL act as source and target: it SHALL translate a `SourceQuery` into `find`, run a `NativeQuery` as an aggregation pipeline, write operations as Extended JSON lines and apply them in a session transaction.

#### Scenario: Neutral query on MongoDB
- **WHEN** a `SourceQuery('tb_aluno', fields=('cod_aluno', 'cpf'), order_by=('cod_aluno',), limit=100)` runs on the MongoDB source
- **THEN** the adapter returns at most 100 documents ordered by `cod_aluno` with only the two fields

#### Scenario: Types preserved in the artifact
- **WHEN** an `Insert` with a date, a `Decimal` and binary data is rendered by the MongoDB target
- **THEN** the `.jsonl` line keeps the three types in Extended JSON
- **AND** the applied document has the same values and types

#### Scenario: Operations without meaning
- **WHEN** `SuspendConstraints`, `ResumeConstraints` or `ResetIdentity` reach the MongoDB target
- **THEN** no command is written for them

#### Scenario: Connection keys
- **WHEN** a MongoDB connection declares `TARGET_URI` and `TARGET_NAME`
- **THEN** the adapter connects with the URI and uses the named database, or the database of the URI when `TARGET_NAME` is empty
- **AND** the manifest records the database without the URI

#### Scenario: Nested field on MongoDB
- **WHEN** a `SourceQuery` asks a MongoDB source for `endereco.cidade`
- **THEN** each row has the value at that path under the key `endereco.cidade`

#### Scenario: Batched inserts keep their command numbers
- **WHEN** the third insert of a batch of consecutive inserts into one collection fails with a duplicate key
- **THEN** the application ends with result `failed` naming that command and its line
- **AND** the report records the MongoDB error code `11000`
- **AND** nothing of the application stays in the target

#### Scenario: Transaction time limit
- **WHEN** a MongoDB session opens
- **THEN** the report records the `transactionLifetimeLimitSeconds` of the server when the user can read it

### Requirement: The same migration runs on different adapters
A migration that uses only `SourceQuery`, operations and strategies SHALL run without changes when its source or its target adapter is replaced in the `.env`.

#### Scenario: Reference client across databases
- **WHEN** the `AcmeEdu` migration runs from PostgreSQL to MongoDB and from MongoDB to PostgreSQL with the same data
- **THEN** both runs end committed with no failing command
- **AND** the check script finds the same people, phones and enrollments in both targets

