## ADDED Requirements

### Requirement: Confirm that cited clauses support each answer

The workbench SHALL verify each generated policy answer against the clauses assigned to that question before presenting the answer as supported.

#### Scenario: Phase or action is outside a cited clause

- **WHEN** a clause describes how the merchant issues a refund and the customer asks to transfer funds after receipt
- **THEN** the workbench marks the question for human review

#### Scenario: Evidence is mixed within one message

- **WHEN** only some questions have directly applicable evidence
- **THEN** the workbench retains verified answers and separately marks unsupported questions for human review

#### Scenario: Additional verification is invalid or unavailable

- **WHEN** a review times out or its supporting excerpt cannot be located in the cited clause
- **THEN** the workbench withholds the unverified conclusion

### Requirement: Preserve applicable retrieval scope

The workbench SHALL keep the established shop filter and evidence budget when adding common policy terminology to a query.

#### Scenario: Search colloquial wording about returning a bundled gift

- **WHEN** the shopper asks whether something included with an order must be sent back
- **THEN** the search can include terms for bundled gifts and returning them within the same shop and existing query budget
