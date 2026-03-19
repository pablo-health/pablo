# Integration Tests

This directory contains integration tests that test the application with real external services (Firestore emulator, LLM APIs, etc).

## Structure

```
tests_integration/
  database/              # Firestore repository tests
  llm/                   # Future: LLM API integration tests
  api/                   # Future: End-to-end API tests
  conftest.py            # Shared fixtures
```

## Running Integration Tests

### Prerequisites

**Install Firebase Tools:**
```bash
npm install -g firebase-tools
```

Or via gcloud SDK:
```bash
gcloud components install cloud-firestore-emulator
```

### Start Firestore Emulator

```bash
firebase emulators:start --only firestore
```

The emulator will start on `localhost:8080` by default.

### Run Tests

In a separate terminal:

```bash
# Set environment variable
export FIRESTORE_EMULATOR_HOST=localhost:8080

# Run integration tests only
make test-integration

# Run all tests (unit + integration)
make test-all
```

## What's Tested

### Database Tests (`database/`)

Tests for `FirestorePatientRepository`:
- ✅ CRUD operations (create, read, update, delete)
- ✅ Multi-tenant isolation (security-critical)
- ✅ Search functionality with Firestore queries
- ✅ Cascade deletion (sessions deleted with patient)
- ✅ Lowercase search field generation

These tests verify actual Firestore behavior, including:
- Query limitations and constraints
- Index requirements
- Real transaction behavior

### Future Tests

- **LLM tests** (`llm/`): Integration with OpenAI, Claude, etc.
- **API tests** (`api/`): End-to-end workflow tests

## CI Integration

See issue THERAPY-6lc for GitHub Actions configuration with Firestore emulator.

## Notes

- Integration tests are slower than unit tests
- They require external services (emulator or real services)
- Unit tests (`backend/tests/`) should still use mocks for speed
- Run `make test` for fast feedback during development
- Run `make test-all` before committing
