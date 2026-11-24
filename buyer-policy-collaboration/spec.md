## ADDED Requirements

### Requirement: Answer an order-specific policy follow-up

The workbench SHALL let a buyer ask a policy question for a known order and answer it using evidence retrieved through MCP.

#### Scenario: Real tool execution and citation

- **WHEN** a buyer asks about damaged packaging for A102
- **THEN** the evidence agent's tool request is validated, the application uses MCP to read the order and policy, and the answer displays QH-03 with its source text

#### Scenario: Model changes the requested order or question

- **WHEN** the model requests a different order, a different question or an unknown tool
- **THEN** the application rejects the call before MCP executes it

#### Scenario: No matching policy

- **WHEN** the MCP policy search finds no text
- **THEN** the workbench reports that no matching clause was found
