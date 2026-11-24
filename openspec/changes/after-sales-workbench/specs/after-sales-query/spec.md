## ADDED Requirements

### Requirement: Evaluate a return window

The application SHALL query a simulated order and evaluate the selected 7-day or 15-day return window in Python using the demo date.

#### Scenario: The same order under two policies

- **WHEN** A102 is queried with the 7-day policy and then the 15-day policy
- **THEN** the result shows eight elapsed days and changes from outside the window to within it

#### Scenario: Missing or unknown order

- **WHEN** a request omits a single order number or references an unknown order
- **THEN** the application requests an order number or reports that the order could not be found, respectively

#### Scenario: Order not signed

- **WHEN** A103 is queried
- **THEN** the application reports that the order has not been signed for

### Requirement: Invoke the lookup tool safely

The application SHALL validate model-proposed tool calls and SHALL use deterministic order data for the result.

#### Scenario: Valid DeepSeek tool call

- **WHEN** DeepSeek proposes the lookup tool with the order ID present in the question
- **THEN** the application evaluates the return window for that order using Python

#### Scenario: Model unavailable

- **WHEN** the API key is absent
- **THEN** the page identifies local demo mode and the same order results can be queried locally

#### Scenario: Invalid tool result

- **WHEN** the model is unavailable or proposes an invalid tool call
- **THEN** the application reports an error without disclosing credentials or inventing order data

### Requirement: Display query evidence

The page SHALL accept typed questions and example orders and present their results with available evidence.

#### Scenario: Query from the page

- **WHEN** the user submits a question or selects a sample order
- **THEN** the page renders the result, and shows the available order and policy evidence
