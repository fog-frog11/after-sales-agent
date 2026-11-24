## ADDED Requirements

### Requirement: Retrieve policy evidence for the ticket's shop

Staff SHALL be able to search policy text from a ticket, and the index query SHALL be scoped to the shop associated with that ticket's order.

#### Scenario: Similar wording in two shops

- **WHEN** staff searches for damaged packaging on a ticket from either shop
- **THEN** only clauses from the associated shop are shown with article identifiers and source text

#### Scenario: No applicable articles

- **WHEN** the index returns no relevant articles
- **THEN** the API reports that no matching clauses were found without generating a policy answer

### Requirement: Cite retrieved articles in the answer

Generated explanations SHALL cite retrieved articles and be displayed alongside their source text.

#### Scenario: Invalid citation or provider failure

- **WHEN** the provider fails or uses an article identifier outside the retrieved evidence
- **THEN** the workbench returns the retrieved source text and asks staff to consult it directly
