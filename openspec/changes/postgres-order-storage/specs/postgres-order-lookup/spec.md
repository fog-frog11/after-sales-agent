## ADDED Requirements

### Requirement: Read the configured PostgreSQL order source

The application SHALL read the order from PostgreSQL when a database URL is configured and expose the active order source in its configuration response.

#### Scenario: Updated signing date changes the return result

- **WHEN** A102 is changed from eight elapsed days to six in PostgreSQL and queried using the seven-day policy
- **THEN** the API reports six elapsed days and a result within the return window

#### Scenario: Idempotent seed

- **WHEN** the initializer is run after changing an existing order's signing date
- **THEN** it preserves the changed date and inserts any missing sample orders

#### Scenario: Database unavailable

- **WHEN** reading the configured database fails
- **THEN** the query returns HTTP 503 with a readable error and does not use sample memory data

#### Scenario: Local demo

- **WHEN** no database URL is configured
- **THEN** the application serves the existing demonstration orders and reports memory as the order source
