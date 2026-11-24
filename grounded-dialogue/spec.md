## ADDED Requirements

### Requirement: Unified item dialogue memory
The system SHALL isolate confirmed facts, corrections and recent messages by buyer, order and item. Facts SHALL be supported by buyer quotes. Resolving an issue SHALL clear associated suggestions without withdrawing a real application.

#### Scenario: Correction through an interruption
- **WHEN** a buyer changes an exchange request into a wrong-item return, asks logistics and then continues
- **THEN** the reason and requested handling persist and obsolete exchange specifications are absent

#### Scenario: New preference after resolving an issue
- **WHEN** a missing part is found and the buyer subsequently wants a change-of-mind return
- **THEN** the old missing-part suggestion is cleared and the new return preference is assessed

### Requirement: Model-directed read-only planning
Recorded natural-language questions SHALL use a model planner when configured. Validated topics SHALL execute only authorized read queries. Network waits SHALL occur outside database locks, and stale conversation versions SHALL be replanned at most once.

#### Scenario: Concurrent update
- **WHEN** a newer message is saved during planning
- **THEN** execution rejects the stale version and obtains a fresh dialogue snapshot

### Requirement: Grounded response generation
Business replies SHALL be composed from verified query facts and validated references. Invalid numbers, unsupported completed actions and unavailable application recommendations SHALL fall back to the verified reply with the generation status saved.

#### Scenario: Missing video
- **WHEN** an identified item has an established issue and the buyer lacks an unboxing video
- **THEN** the reply uses the real submission requirements without asking which item is involved

### Requirement: Scoped hybrid retrieval
Purchase-policy queries SHALL constrain shop, item, purchase edition and approved supplement dates before Milvus BM25, E5, RRF and evidence-window ranking. Returned text SHALL match the trusted database snapshot. Dependency failures SHALL degrade and allow later recovery.

#### Scenario: Foreign or stale evidence
- **WHEN** retrieval returns another scope or changed source text
- **THEN** it is excluded from the cited evidence

### Requirement: Reproducible conversation acceptance
The project SHALL retain independent dialogue questions, live-provider reports and three-role browser acceptance. Reports SHALL distinguish generation fallbacks, state correctness and latency from universal accuracy claims.

#### Scenario: Existing application
- **WHEN** a buyer submits a return and then asks its progress in the original chat
- **THEN** the reply links the existing case and the shop sees the same application
