## ADDED Requirements

### Requirement: Review and resolve a pending ticket

The application SHALL let staff view pending tickets, show the selected ticket's order evidence, and change its state to resolved with a note.

#### Scenario: Staff reviews a pending ticket

- **WHEN** staff opens a pending ticket
- **THEN** the detail shows the submission reason, associated order and current policy evidence

#### Scenario: Competing resolutions

- **WHEN** two resolution requests target the same pending ticket
- **THEN** only one succeeds and the saved note and timestamp remain available

### Requirement: Look up buyer progress

The workbench SHALL provide a progress lookup by ticket number.

#### Scenario: Look up a resolved ticket

- **WHEN** a buyer enters the number of a resolved ticket
- **THEN** the workbench displays its resolved state, note and time from PostgreSQL

#### Scenario: Unknown number

- **WHEN** a buyer enters a number that is not assigned
- **THEN** the workbench shows that the ticket could not be found
