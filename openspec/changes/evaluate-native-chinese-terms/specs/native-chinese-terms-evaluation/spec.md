## ADDED Requirements

### Requirement: Index identifiable source clauses

The evaluator SHALL retain each clause's stable identifier and original source URL.

#### Scenario: Parse a rule that contains a table

- **WHEN** an official rule carries fee logic in a membership table
- **THEN** indexed text retains the row labels and membership columns in their original order

#### Scenario: Parse the numbered public regulation

- **WHEN** the regulation is parsed from its dated official snapshot
- **THEN** the evaluator validates the continuous sequence of article numbers and reports the file hash

### Requirement: Measure retrieval and answers within the applicable scope

The evaluator SHALL compare retrieval strategies under the same source scope and optionally validate generated replies.

#### Scenario: Similar terms occur under incompatible seller channels

- **WHEN** a case represents a self-operated or marketplace order
- **THEN** scoped strategies use only the trusted channel and general clauses and the report records any cross-scope candidates

#### Scenario: Test unsupported requests

- **WHEN** a case labels a question as having no applicable clause in the corpus
- **THEN** the report tracks incidental candidates and tests whether the optional answer evaluation returns the expected no-match status
