## ADDED Requirements

### Requirement: Confirm support ticket before creating it

The workbench SHALL preview a support ticket for an existing order and create it only after explicit confirmation.

#### Scenario: Preview then confirm

- **WHEN** a user previews a known order and a reason
- **THEN** the workbench returns that order, reason and a confirmation ID without creating a ticket
- **AND WHEN** the user confirms the same ID
- **THEN** the workbench saves a pending ticket and returns its ticket number

#### Scenario: Retry confirmation

- **WHEN** the same confirmation ID is submitted again
- **THEN** the API returns the existing ticket number

#### Scenario: Unknown order or expired preview

- **WHEN** the order does not exist or a confirmation ID has no saved ticket or pending preview
- **THEN** the API returns a clear error and creates no ticket

#### Scenario: Storage temporarily unavailable

- **WHEN** the first confirmation fails to write to PostgreSQL
- **THEN** the confirmed preview can be retried using the same confirmation ID
