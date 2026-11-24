## ADDED Requirements

### Requirement: Show progress inside the buyer conversation

The workbench SHALL show the submitted message and a progress indicator in the conversation while awaiting a response.

#### Scenario: The order or policy lookup is slow

- **WHEN** a buyer sends a message and the request is still pending
- **THEN** the conversation displays the buyer's text and a waiting assistant bubble
- **AND** the confirmed response replaces the waiting content when it arrives

#### Scenario: The response cannot be obtained

- **WHEN** the request fails
- **THEN** the conversation displays the failure in the assistant bubble and restores the submitted text for retry
- **AND** retrying replaces the pending content without adding another copy of the failed turn
