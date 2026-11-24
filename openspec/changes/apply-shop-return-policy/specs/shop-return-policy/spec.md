## ADDED Requirements

### Requirement: Apply the policy associated with an order

The workbench SHALL use the return policy stored for the order's shop and show the policy name and allowed days in the result.

#### Scenario: Two orders with the same elapsed days

- **WHEN** A102 and A104 are queried eight days after delivery
- **THEN** A102 is outside the seven-day window and A104 is eligible under the fifteen-day policy

#### Scenario: User supplies an order ID after a follow-up

- **WHEN** a user first asks without an order ID and then supplies A104
- **THEN** the resumed query uses A104's fifteen-day policy

#### Scenario: The order has no policy

- **WHEN** a known order has no policy association
- **THEN** the workbench explains that no eligibility decision can be made

#### Scenario: Seed the existing database

- **WHEN** the policy migration is applied twice to an existing database
- **THEN** existing delivery dates and policy edits are preserved
