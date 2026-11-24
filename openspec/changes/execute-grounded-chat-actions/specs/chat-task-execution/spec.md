## ADDED Requirements

### Requirement: Typed business facts
The assistant SHALL generate replies from typed product, eligibility, finance, case and task fields. Modern generation SHALL NOT derive facts by splitting a prewritten answer. Unknown shipping costs and unverified bank arrival SHALL remain unknown. Photo upload capability SHALL NOT be described as video upload.

#### Scenario: Product question after a return request
- **WHEN** a buyer asks how many units were purchased
- **THEN** the reply uses purchased units without changing the number of units requested for after-sales service

### Requirement: Persistent task memory
Memory SHALL preserve separately identified issues, their resolution states, quantity, target specification, reported evidence and paused handling intention. Reported evidence SHALL NOT count as uploaded evidence. A change to one issue SHALL NOT resolve another active issue.

#### Scenario: Partial resolution after interruptions
- **WHEN** a missing cable is found while the main product still fails, even after recent-message truncation
- **THEN** the missing-part issue is resolved by its stable identifier and the main product issue remains active

### Requirement: Explicit in-chat confirmation
The assistant MAY prepare an application or an available existing-case action. Execution SHALL require an authenticated confirmation of a server-stored card. The buyer SHALL NOT override its order, quantity, handling, refund amount, prompt version or action through the confirmation body.

#### Scenario: User or merchant changes the requested handling
- **WHEN** quantity, intent, eligibility or the merchant proposal changes before confirmation
- **THEN** the old card is rejected and the buyer must review a current card

### Requirement: Atomic execution and receipts
Confirmation SHALL reuse the existing business state machine within one database transaction. Case changes, event records and the confirmation receipt SHALL commit together. Duplicate confirmation SHALL return the same receipt, and changed input SHALL be rejected. Other pending cards for that buyer and item SHALL become outdated.

#### Scenario: Refresh and double click
- **WHEN** the buyer confirms twice or reloads the conversation
- **THEN** only one case operation exists and its receipt remains visible without an obsolete new-application form

### Requirement: Measured retrieval strategy
BM25, E5, fusion and window ordering SHALL be evaluated on identical questions. Default ordering SHALL follow measured evidence rather than an assumption that reranking improves results. Persisted dense indexing SHALL use a separate collection and preserve existing collections.

#### Scenario: Selecting a measured combination
- **WHEN** direct window ordering regresses but combining window ranks with the original fusion ranks performs better on the primary questions and preserves coverage on separately authored questions
- **THEN** the measured combination may become the default while the direct window strategy remains a comparison option

#### Scenario: Preserving independent lexical recall
- **WHEN** Milvus BM25 ranks the trusted candidates differently from scoped lexical recall
- **THEN** both lexical rankings and the dense ranking participate in fusion, and foreign identifiers or changed source text cannot participate

#### Scenario: Short clauses and unsupported labels
- **WHEN** a clause window equals its already encoded full text or a benchmark question has no supported source labels
- **THEN** the window reuses the existing vector, and the unsupported question is reported separately from supported-question coverage

### Requirement: Bounded plan repair
Invalid semantic tool parameters MAY be corrected by one further model request using schema feedback. A second invalid plan SHALL fall back, and neither plan SHALL grant write permission.

#### Scenario: Misplaced evidence field
- **WHEN** the model places evidence at the tool argument root instead of inside observation
- **THEN** it receives schema feedback once and only a subsequently valid plan can update task memory

### Requirement: Focused reply context
The writer SHALL receive common factual boundaries and only the instructions applicable to current typed facts. Missing fields SHALL be distinguished from already preserved task fields. An interruption SHALL NOT imply resuming a paused task.

#### Scenario: Logistics during a paused application
- **WHEN** a buyer asks about original logistics while handling is paused
- **THEN** logistics is answered without requesting another application or changing preserved handling intention

### Requirement: Per-aspect evidence coverage
Evidence coverage SHALL reflect final source checks for each question, including checks that prevent supplemental text from overriding base financial authority. A supported question SHALL NOT grant support to another unsupported question.

#### Scenario: Return postage and an undocumented membership benefit
- **WHEN** the same message asks for documented return postage rules and an undocumented membership promise
- **THEN** the reply distinguishes the supported rule from the unverified promise

### Requirement: Bounded reply generation
Generation, corrective rewriting and optional factual review SHALL share a twelve-second stage budget. Exhaustion SHALL preserve verified fallback rather than expose unchecked text. This stage limit SHALL NOT be represented as an end-to-end latency guarantee.

#### Scenario: Slow correction
- **WHEN** initial generation consumes most of the stage budget
- **THEN** the next request receives only the remaining budget or verified fallback is returned

### Requirement: Bounded read followup
Insufficient purchase-policy evidence MAY trigger one model decision and at most one additional search. The model SHALL NOT change purchase scope or invoke a write. Rewriting SHALL preserve recognized aspects from the original user question as well as the first search. Failure or a worse result SHALL preserve the first verified result.

#### Scenario: A rewrite omits an unsupported benefit
- **WHEN** a second search finds postage evidence by dropping an original membership question
- **THEN** the second result cannot replace the first as completely supported

### Requirement: Distinct handling prerequisites
The writer SHALL distinguish waiting for a shop return address, buyer return tracking and buyer confirmation of an outbound destination. It SHALL NOT claim that explaining these fields submits them.

#### Scenario: Shop has not supplied a return address
- **WHEN** a buyer asks how to send the product back
- **THEN** the assistant identifies the shop prerequisite without requesting a premature tracking number

### Requirement: Confirmed fulfillment drafts
Buyer-provided carrier, tracking and outbound destination fields MAY be collected across turns. Every new field SHALL have literal current-user evidence, SHALL be bound to one case and SHALL be validated by existing operation schemas before preparation. Tracking prefixes SHALL NOT establish an unstated carrier. Execution SHALL require explicit confirmation and reuse the original ship or destination operation.

#### Scenario: Carrier supplied after a tracking number
- **WHEN** a buyer provides a tracking number without a carrier and then supplies the carrier in the next turn
- **THEN** only the missing carrier is requested, the same-case fields prepare one confirmation card and repeated confirmation creates one event

### Requirement: Live recovery and bounded business reads
Conversation history SHALL retain immutable execution receipts while returning current case ownership and permissions. Invalid old cards SHALL be displayed as outdated. A model MAY observe a buyer-owned case and request up to three additional whitelisted read topics, without supplying an order identifier or write arguments.

#### Scenario: Destination confirmed and shop has not dispatched
- **WHEN** an outbound parcel exists in ready stage after address confirmation
- **THEN** recovery reports waiting for shop dispatch rather than waiting for buyer address confirmation
