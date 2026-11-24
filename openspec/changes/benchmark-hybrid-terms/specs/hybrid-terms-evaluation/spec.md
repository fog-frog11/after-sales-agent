## ADDED Requirements

### Requirement: Compare retrieval and ranking on one rulebook

The evaluator SHALL compare BM25, dense retrieval, reciprocal rank fusion and evidence-window reranking for the same public questions and applicable rulebook.

#### Scenario: Fuse overlapping retrieval paths

- **WHEN** BM25 and dense retrieval return the same clause
- **THEN** its ranking in each path contributes to its fusion score and the clause appears once

#### Scenario: Rerank an evidence-bearing candidate

- **WHEN** a clause in the bounded fused candidate set contains a highly relevant window
- **THEN** the window may affect its final rank and the output retains the original clause ID

#### Scenario: Reproduce the evaluation

- **WHEN** the evaluator runs against a preloaded collection
- **THEN** it records corpus and model hashes, per-task candidate order, ranking metrics and latency

#### Scenario: Compare evidence for multiple questions in one message

- **WHEN** the evaluator runs the native Chinese holdout against the preloaded collection
- **THEN** each strategy uses the same applicable sources and the evaluator records coverage for each annotated item
- **AND** it distinguishes missing candidates from candidates omitted from an item's evidence
- **AND** for the fixed answer sample, it checks whether unsupported items are referred for human review
