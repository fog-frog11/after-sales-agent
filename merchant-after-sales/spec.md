## ADDED Requirements

### Requirement: Order entry opens a scoped conversation

The system SHALL scope conversation history by buyer and trusted merchant identity. An order without an integrated merchant identity SHALL use its own scope.

#### Scenario: Opening a different shop

- **WHEN** the buyer opens after-sales from an authorized order in a different shop
- **THEN** the system switches to that shop and shows only its existing history, if any
- **AND** the system does not send the order ID as an answer to a pending question

#### Scenario: Switching orders in the same shop

- **WHEN** the buyer opens a different order in the same shop
- **THEN** the system keeps that shop history and binds subsequent questions to the selected order

### Requirement: Merchant request precedes platform intervention

The system SHALL accept a buyer's explicit shop request for an authorized merchant order, and SHALL require a saved shop request before allowing a new platform ticket preview for that order.

#### Scenario: Merchant handles an application

- **WHEN** the merchant answers an application for its own shop
- **THEN** the buyer can read the saved response
- **AND** a different merchant cannot read or answer that application

#### Scenario: Unresolved issue is escalated

- **WHEN** the buyer submits a confirmed platform ticket after applying to the shop
- **THEN** the platform agent can read the shop request and its available response from the ticket detail

### Requirement: Buyers can follow after-sales progress from an order

The system SHALL return the authorized order's shop request and current platform ticket to its buyer. Ticket selection SHALL prefer a pending ticket and otherwise show the latest completed ticket.

#### Scenario: Buyer views the order after a platform decision

- **WHEN** the buyer opens after-sales progress on their order
- **THEN** the page shows the available shop request, shop response, platform submission, and platform decision in time order

#### Scenario: Buyer repeats a pending escalation

- **WHEN** the buyer previews or confirms another escalation while the order has a pending platform ticket
- **THEN** the system returns the existing ticket and subsequent confirmation retries refer to the same ticket

### Requirement: A buyer can share a verified order in the shop chat

The system SHALL preview the selected order in its shop chat and persist a card only after the buyer sends it. Later questions SHALL use the active order from the most recent card or an explicit order in the buyer's message.

#### Scenario: A buyer enters the shop from order detail

- **WHEN** a buyer opens the chat for one of their orders
- **THEN** preparing the card does not change the previous active order or send a chat message
- **AND** sending the card makes it visible in history and attaches that order to subsequent questions

#### Scenario: A buyer asks what they purchased

- **WHEN** the buyer asks about items on an Olist order with available details
- **THEN** the assistant states the recorded categories and counts
- **AND** the buyer can expand the authorized order detail directly below the card or reply

#### Scenario: A shop handles an application linked to its order

- **WHEN** a shop operator opens the related order from their application queue
- **THEN** the order is accessible only to the shop assigned to that request

### Requirement: Buyers can request human service inside the order chat

The system SHALL expose human service in a verified order chat through a button and an explicit natural-language request. Shop orders with an available merchant service SHALL keep the merchant application step.

#### Scenario: A buyer submits a human review after delivery

- **WHEN** the buyer opens human review in the chat of a delivered historical order and confirms the preview
- **THEN** the current chat shows the resulting ticket and the order's after-sales timeline shows the same pending ticket
- **AND** after platform staff saves an answer, the result is visible from both progress views

### Requirement: Buyers can select a saved conversation

The buyer navigation SHALL open an account-scoped list of saved conversations. Selecting an entry SHALL restore that conversation by ID, and starting a new conversation SHALL open a blank chat.

#### Scenario: A buyer has multiple conversations

- **WHEN** the buyer opens the conversation list and chooses an earlier entry
- **THEN** the list is ordered by recent activity and the chosen entry opens its own history
- **AND** another buyer cannot list or restore that entry
