## ADDED Requirements

### Requirement: Evaluate the local workbench using fixed scenarios

The runner SHALL verify key after-sales scenarios against the running local workbench and report each result.

#### Scenario: Run with local services enabled

- **WHEN** the runner executes against the configured workbench
- **THEN** it checks order decisions, conversation restoration, shop-specific evidence, missing evidence and repeated ticket confirmation

#### Scenario: Change breaks one workflow

- **WHEN** a case returns an unexpected result or fails
- **THEN** subsequent cases run, the report describes the failed case, and the command exits unsuccessfully

#### Scenario: Repeat the suite

- **WHEN** the runner confirms the same already-submitted demo ticket multiple times
- **THEN** it verifies the original ticket number and status remain unchanged
