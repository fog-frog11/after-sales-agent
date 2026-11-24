## ADDED Requirements

### Requirement: Discover and invoke local support tools

A local MCP client SHALL be able to initialize the stdio server and discover the supported read-only tools with input and output schemas.

#### Scenario: Read an order

- **WHEN** the client invokes get_order for A102
- **THEN** the result contains its signing date, shop name and applicable return window

#### Scenario: Retrieve a policy by order

- **WHEN** the client calls search_shop_policy for A102 with a damaged-packaging question
- **THEN** the response contains the retrieved article ID, source, title and text for the associated shop

#### Scenario: Invalid or missing data

- **WHEN** an invalid or nonexistent order is submitted
- **THEN** the tool provides an actionable error and performs no order or policy change
