## ADDED Requirements

### Requirement: Import the existing Olist dataset as usable orders

The workbench SHALL import usable orders, matching item rows and seller relationships from the available Olist files.

#### Scenario: Reapply the import

- **WHEN** the same dataset is imported again
- **THEN** stable order IDs and references remain valid and duplicate items are rejected by identity

### Requirement: Retrieve public after-sales references for verified Olist orders

The workbench SHALL use an independent Brazilian public-reference scope for verified Olist orders and display the nature of the sources.

#### Scenario: One message asks about items and public rules

- **WHEN** a buyer sends an owned Olist order card and asks about its items and return rules
- **THEN** the workbench reads actual item records and consults Brazilian public guidance alongside them

#### Scenario: Asking about a private merchant promise

- **WHEN** a question concerns a seller-specific promise with insufficient evidence
- **THEN** the workbench marks that question for human review

#### Scenario: Staff reviews an existing Olist ticket

- **WHEN** platform staff searches applicable rules for an existing Olist ticket
- **THEN** the ticket order determines the public-reference scope
