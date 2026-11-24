## ADDED Requirements

### Requirement: Allocate clause evidence for explicitly separated requests

The retrieval path SHALL preserve the first three whole-query candidates and may supplement them using up to three explicit aspects within the same verified scope. The combined evidence set SHALL contain at most five distinct clauses.

#### Scenario: A single request

- **WHEN** the input does not contain separable aspects
- **THEN** the system performs one retrieval using its existing three-clause limit

#### Scenario: An additional lookup fails

- **WHEN** an aspect query fails after the whole-query retrieval succeeds
- **THEN** the system continues with the whole-query candidates

### Requirement: Validate each answered aspect independently

Each answered aspect SHALL cite at least one clause assigned to that aspect. An unsupported or invalid aspect SHALL be marked for human review.

#### Scenario: Some aspects are unsupported

- **WHEN** an aspect lacks valid support but another aspect has valid citations
- **THEN** the response preserves the supported answer and displays review guidance only for the unsupported aspect

#### Scenario: A citation belongs to a different aspect

- **WHEN** a citation is available in the request but absent from that aspect's permitted clauses
- **THEN** the item cannot carry that citation in an answered response
