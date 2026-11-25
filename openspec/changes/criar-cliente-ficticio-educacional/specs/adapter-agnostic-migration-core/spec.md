## ADDED Requirements

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
