## ADDED Requirements

### Requirement: Apply for after-sales service for a purchased item

The workbench SHALL take the order, item, quantity, service type and reason from an authenticated buyer's confirmed submission.

#### Scenario: The buyer retries or selects an excessive quantity

- **WHEN** a buyer submits a request or retries the same submission identifier
- **THEN** an identical retry returns the persisted request
- **AND** a new request cannot use another buyer's order, exceed the eligible quantity, or overlap an active request for the same item

### Requirement: Record each after-sales stage

The workbench SHALL transition a service request through the allowed stages and persist a timestamped event for each applied stage.

#### Scenario: Staff approves and handles the return package

- **WHEN** staff approves a pending request, the buyer registers the shipment, and staff confirms receipt and completes handling
- **THEN** the buyer can view the same chronological events from the order and its service request

#### Scenario: A review is rejected or a stage is skipped

- **WHEN** staff rejects a pending request or a user attempts a stage that does not match the current state
- **THEN** rejection requires an explanation and invalid transitions leave the timeline unchanged

### Requirement: Answer verified return charges and track service progress

The workbench SHALL bind freight answers and service progress to the active order and identify any practice data.

#### Scenario: Some freight information is absent

- **WHEN** an order lacks a return reason or verified charges
- **THEN** the assistant asks for the reason if needed, or identifies what still needs verification
- **AND** the buyer can open the service application from the relevant response
