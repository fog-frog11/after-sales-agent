## ADDED Requirements

### Requirement: Evaluate retrieval against public retail tasks

The evaluator SHALL retrieve public clauses using the original public retail request and score the returned clause IDs against a documented action-to-section mapping.

#### Scenario: Multiple actions yield multiple target clauses

- **WHEN** a task contains more than one supported action or one action requires multiple sections
- **THEN** the scoring records all distinct mapped clause IDs and evaluates partial and complete retrieval separately

#### Scenario: Public source data changes

- **WHEN** a mapped section is missing from the supplied source document
- **THEN** the runner reports the mismatch before scoring

### Requirement: Isolate and report retrieval scale experiments

The evaluator SHALL keep experiments in content-derived Milvus collections and report source hashes, corpus size, per-task retrieval results and load metrics.

#### Scenario: Compare retrieval after increasing corpus size

- **WHEN** a ShopTC sample count is provided
- **THEN** the same public questions run over the original and expanded collections and the expanded collection receives the concurrent requests

#### Scenario: Milvus fails to serve a constructed query

- **WHEN** a Milvus search fails during a scored or concurrent request
- **THEN** the runner includes the failed request in the report and exits with an unsuccessful status

### Requirement: Evaluate a trusted rulebook scope on the expanded collection

The evaluator SHALL compare the shared benchmark shop scope against a scope that also requires the known rulebook's exact source.

#### Scenario: Compare search with a known applicable rulebook

- **WHEN** the benchmark uses a corpus expanded with ShopTC clauses
- **THEN** both searches use the same collection and questions, the scoped search applies shop and source filtering, and the report records its eligible article count

#### Scenario: Reuse a previously loaded collection in read-only mode

- **WHEN** the evaluator is invoked in read-only mode
- **THEN** it checks and searches existing collections without loading, creating or writing them and stops if a collection is missing or its article count differs from the prescribed corpus
