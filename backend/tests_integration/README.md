# Integration Tests

This directory contains integration tests that test the application with real external services (PostgreSQL, LLM APIs, etc).

## Structure

```
tests_integration/
  database/              # PostgreSQL repository tests
  llm/                   # Future: LLM API integration tests
  api/                   # Future: End-to-end API tests
  conftest.py            # Shared fixtures
```

## Running Integration Tests

### Prerequisites

A running PostgreSQL instance (e.g., via docker-compose):
```bash
docker compose up -d postgres
```

### Run Tests

```bash
# Run integration tests only
make test-integration

# Run all tests (unit + integration)
make test-all
```

## What's Tested

### Database Tests (`database/`)

Tests for PostgreSQL repository implementations:
- CRUD operations (create, read, update, delete)
- Multi-tenant isolation (security-critical)
- Search functionality
- Cascade deletion (sessions deleted with patient)

### Future Tests

- **LLM tests** (`llm/`): Integration with Gemini, etc.
- **API tests** (`api/`): End-to-end workflow tests

## Notes

- Integration tests are slower than unit tests
- They require external services (database or real services)
- Unit tests (`backend/tests/`) should still use mocks for speed
- Run `make test` for fast feedback during development
- Run `make test-all` before committing
