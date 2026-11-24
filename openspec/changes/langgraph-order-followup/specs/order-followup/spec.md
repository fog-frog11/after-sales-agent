## ADDED Requirements

### Requirement: Continue a return query across HTTP requests

The workbench SHALL ask for an order ID when one cannot be determined and use the saved return policy when resuming the query.

#### Scenario: Supply the order ID in a second turn

- **WHEN** a user asks a return question under the 15-day policy and then supplies A102 under the same conversation ID
- **THEN** the system queries A102 and uses the original 15-day policy

#### Scenario: Supply another ambiguous reply

- **WHEN** the supplied reply does not identify a single order
- **THEN** the system asks again under the same conversation ID

#### Scenario: Retry after a temporary lookup failure

- **WHEN** the resumed order lookup fails temporarily and the user submits the same reply again
- **THEN** the system retries using the saved question and policy

#### Scenario: Use an unknown or finished conversation ID

- **WHEN** a user submits an unknown or finished conversation ID
- **THEN** the API returns 409 and asks them to start a new query
