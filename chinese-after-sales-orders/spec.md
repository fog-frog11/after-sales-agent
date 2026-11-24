## ADDED Requirements

### Requirement: Browse Chinese e-commerce research orders

The workbench SHALL open domestic research orders by default, using stored quantities, CNY prices and linked delivery records.

#### Scenario: A field is missing from the source

- **WHEN** an order has no matched delivery evidence or freight amount
- **THEN** the workbench shows that the relevant field is unavailable
- **AND** it does not infer that the order is undelivered or that freight is zero

### Requirement: Bind evidence retrieval to the verified order channel

The workbench SHALL use a dedicated Chinese public-rule collection and resolve its source filter from the verified order record.

#### Scenario: Two orders use different sales channels

- **WHEN** the buyer asks about return freight for self-operated and third-party domestic orders
- **THEN** each answer uses only the corresponding published sources and general Chinese rules

### Requirement: Continue domestic orders through the existing ticket workflow

The workbench SHALL allow a verified domestic order to be previewed, confirmed and processed through the existing ticket queue.

#### Scenario: Staff processes an existing customer request

- **WHEN** a buyer confirms a domestic-order ticket and staff saves a resolution
- **THEN** the resolution becomes available in that order's after-sales progress
