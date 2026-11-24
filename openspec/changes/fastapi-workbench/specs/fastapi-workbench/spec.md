## ADDED Requirements

### Requirement: Serve the existing workbench through FastAPI

The application SHALL serve the existing workbench and its query and configuration endpoints at their current URLs.

#### Scenario: Query after migration

- **WHEN** the workbench submits A102 and the seven-day policy
- **THEN** the query returns the existing mode and result structure

#### Scenario: Malformed query or dependency failure

- **WHEN** the query body is invalid, DeepSeek fails, or the order store fails
- **THEN** the API returns 400, 502, or 503 respectively, using a message field

#### Scenario: Explore the interface documentation

- **WHEN** a developer opens /docs or /openapi.json
- **THEN** the interface documentation exposes the query schema and its supported error responses
