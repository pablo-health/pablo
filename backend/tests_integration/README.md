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

## Live vendor lane

`clearinghouse_live/` runs the claims adapter (`app/claims/stedi.py`)
against the clearinghouse's **test mode** with your own clearinghouse test
API key. The recorded fixtures under `tests/fixtures/clearinghouse/` prove
our code; this lane proves the vendor still answers that way — every live
response is shape-diffed (keys and JSON types, not values) against its
recording, so a dropped or retyped field fails with the offending paths.

What it exercises, in order: payer directory search; mock eligibility
(active, inactive, and the AAA error path); three claims the vendor's edits
reject on purpose, each paired with the local pre-flight in
`app/claims/validation.py` that would have caught it; one accepted
test-payer claim with an `Idempotency-Key` (replay returns the same claim,
a changed body is refused with 422); the 277CA and 835 that follow it,
polled from the transaction feed, fetched from the report endpoints and
parsed with `app/claims/responses.py`; and the enrollment surface,
read-only.

```bash
# Opt in by exporting your test-mode key for this shell only — never put it
# in a file. The lane refuses a production-mode key at collection time.
export CLEARINGHOUSE_LIVE_API_KEY="$(your-secret-store read clearinghouse-test-key)"
make test-clearinghouse-live
```

Without the variable the lane skips with a reason. Each run submits four
claims to the vendor's test payer (one accepted, three rejected) and nothing
reaches a real payer. The vendor's test mode does not support transaction
enrollment, so the enrollment test pins the documented `403 access_denied`
rather than reading the enrollment; the provider record and test-payer
enrollment the fixtures describe already exist on the account and are never
created here.

Drift handling is deliberate: this lane never re-records a fixture. If a
shape-diff fails, a person looks at the vendor's change, updates the parser
or model if it matters, and re-records the fixture by hand.
