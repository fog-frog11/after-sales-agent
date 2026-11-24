## ADDED Requirements

### Requirement: Compare clause coverage on a pinned Chinese evaluation set

The evaluator SHALL score Chinese task rewrites using the public task's original action markings and measure irrelevant retrieval on no-evidence questions.

#### Scenario: Review multiple required clauses

- **WHEN** a Chinese task has multiple target clauses
- **THEN** the report records whether the first three results contain all mapped clauses and preserves the candidate rankings

### Requirement: Handle a reply that has no applicable clause

The clause reply handler SHALL return its existing no-match response when all supplied candidates are explicitly judged inapplicable.

#### Scenario: The retrieved candidate refers only to background wording

- **WHEN** the writer signals that no clause addresses any requested outcome
- **THEN** the API indicates no applicable match, omits the irrelevant candidates, and asks the buyer to confirm with a human
