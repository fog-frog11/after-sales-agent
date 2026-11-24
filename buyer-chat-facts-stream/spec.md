## ADDED Requirements

### Requirement: Show verified order facts before policy answers

The workbench SHALL send available order facts before starting policy generation for a mixed question.

#### Scenario: A buyer asks for item quantity and return rules

- **WHEN** the request includes an authorized order facts query and an after-sales policy question
- **THEN** the assistant bubble shows the verified facts while the policy lookup and review continue
- **AND** the final response completes the same turn and becomes available in the conversation history

#### Scenario: A buyer asks only for an explicit order fact

- **WHEN** the request is a clearly recognized JD order facts query
- **THEN** the workbench uses the allowed local lookup and returns the final answer directly

#### Scenario: A different buyer's order is entered

- **WHEN** the supplied order is outside the caller's granted scope
- **THEN** no order facts are emitted and the final response does not reveal the order

#### Scenario: The stream stops after verified facts reach the buyer

- **WHEN** the partial result has arrived and the request fails before the final response
- **THEN** the assistant bubble keeps the verified facts visible and provides a retry message
