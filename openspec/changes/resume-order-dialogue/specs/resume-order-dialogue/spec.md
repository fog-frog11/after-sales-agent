## ADDED Requirements

### Requirement: Resume an order dialogue after reload or restart

The application SHALL restore the waiting prompt and original question using a saved conversation ID.

#### Scenario: Recover while awaiting an order number

- **WHEN** an order dialogue is waiting for an order number and its page is reloaded in the same tab
- **THEN** the original question and order-number prompt are restored

#### Scenario: Resume after service restart

- **WHEN** the service restarts with the same PostgreSQL checkpoint storage and receives the saved conversation ID and order number
- **THEN** the existing dialogue proceeds with the original question

#### Scenario: Retry a finished reply

- **WHEN** the same reply is submitted again to the completed conversation
- **THEN** the service returns its saved result

#### Scenario: Unknown conversation

- **WHEN** the saved conversation ID cannot be read from an available storage backend
- **THEN** the page asks the user to start a new query
