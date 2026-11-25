## MODIFIED Requirements

### Requirement: Metadata operations are provider-backed
The migration core SHALL perform auxiliary target metadata operations through a `MetadataProvider` contract instead of embedding PostgreSQL catalog queries in `BaseMigration` or Acme code.

#### Scenario: Trigger commands are generated for PostgreSQL target
- **WHEN** the default PostgreSQL target prepares trigger disable and enable commands
- **THEN** the commands are provided by the PostgreSQL metadata provider

#### Scenario: Reference map is loaded from target
- **WHEN** an Acme declares a reference map in `declare_reference_maps()`
- **THEN** the key and value pairs are read through the configured metadata provider before the `prepare_target` phase opens
- **AND** the loaded map is cached by the migration context under the declared name
