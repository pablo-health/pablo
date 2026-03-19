# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Shared test fixtures for Patient API tests."""

import os

# Set environment to development for all tests
# CRITICAL: This must be set BEFORE importing app to ensure development mode
os.environ["ENVIRONMENT"] = "development"
# Enable SaaS features so admin/tenant routes are registered for tests
os.environ["PABLO_EDITION"] = "solo"

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.auth.firebase_init import initialize_firebase_app
from app.auth.service import (
    get_current_user,
    get_current_user_id,
    get_current_user_no_mfa,
    require_admin,
    require_baa_acceptance,
    require_mfa,
)
from app.main import app
from app.models import User
from app.repositories import (
    InMemoryAllowlistRepository,
    InMemoryPatientRepository,
    InMemoryTherapySessionRepository,
    InMemoryUserRepository,
    get_allowlist_repository,
    get_user_repository,
)
from app.routes.patients import get_patient_repository, get_therapy_session_repository
from app.routes.sessions import (
    get_patient_repository as get_sessions_patient_repository,
)
from app.routes.sessions import (
    get_session_repository,
)
from app.services import AuditService, get_audit_service
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def mock_firebase_init() -> Any:
    """Prevent Firebase Admin SDK from making real network calls in tests.

    Patches at the source module AND at the import location in service.py
    so both direct and re-exported references are covered. Also clears
    the lru_cache to avoid stale state between tests.
    """
    initialize_firebase_app.cache_clear()

    with (
        patch("app.auth.firebase_init.initialize_firebase_app") as mock_init,
        patch("app.auth.service.initialize_firebase_app", mock_init),
    ):
        mock_init.return_value = MagicMock()
        yield mock_init


@pytest.fixture
def mock_session_repo() -> InMemoryTherapySessionRepository:
    """Create a fresh in-memory session repository for each test."""
    return InMemoryTherapySessionRepository()


@pytest.fixture
def mock_repo(mock_session_repo: InMemoryTherapySessionRepository) -> InMemoryPatientRepository:
    """Create a fresh in-memory repository for each test."""
    return InMemoryPatientRepository(session_repo=mock_session_repo)


@pytest.fixture
def mock_user_id() -> str:
    """Default test user ID."""
    return "test-user-123"


@pytest.fixture
def mock_user(mock_user_id: str) -> User:
    """Create a test user with BAA accepted."""
    return User(
        id=mock_user_id,
        email="test@example.com",
        name="Test Therapist",
        created_at="2024-01-01T00:00:00Z",
        baa_accepted_at="2024-01-01T00:00:00Z",
        baa_version="2024-01-01",
    )


@pytest.fixture
def mock_user_repo() -> InMemoryUserRepository:
    """Create a fresh in-memory user repository for each test."""
    return InMemoryUserRepository()


@pytest.fixture
def mock_allowlist_repo() -> InMemoryAllowlistRepository:
    """Create a fresh in-memory allowlist repository for each test."""
    return InMemoryAllowlistRepository()


@pytest.fixture
def mock_audit_service() -> AuditService:
    """Create a mock audit service that doesn't write to Firestore."""
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value.set = MagicMock()
    return AuditService(mock_db)


@pytest.fixture
def client(
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_user_id: str,
    mock_user: User,
    mock_user_repo: InMemoryUserRepository,
    mock_allowlist_repo: InMemoryAllowlistRepository,
    mock_audit_service: AuditService,
) -> Any:
    """Create a TestClient with mocked dependencies."""
    # Override dependencies
    app.dependency_overrides[get_patient_repository] = lambda: mock_repo
    app.dependency_overrides[get_sessions_patient_repository] = lambda: mock_repo
    app.dependency_overrides[get_therapy_session_repository] = lambda: mock_session_repo
    app.dependency_overrides[get_session_repository] = lambda: mock_session_repo
    app.dependency_overrides[get_current_user_id] = lambda: mock_user_id
    app.dependency_overrides[require_mfa] = lambda: {"uid": mock_user_id, "firebase": {}}
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_user_no_mfa] = lambda: mock_user
    app.dependency_overrides[require_baa_acceptance] = lambda: mock_user
    app.dependency_overrides[require_admin] = lambda: mock_user
    app.dependency_overrides[get_user_repository] = lambda: mock_user_repo
    app.dependency_overrides[get_allowlist_repository] = lambda: mock_allowlist_repo
    app.dependency_overrides[get_audit_service] = lambda: mock_audit_service

    # Create client
    test_client = TestClient(app)

    yield test_client

    # Cleanup
    app.dependency_overrides.clear()


@pytest.fixture
def sample_patient_data() -> dict[str, Any]:
    """Sample valid patient data for testing."""
    return {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@example.com",
        "phone": "(555) 123-4567",
        "status": "active",
        "date_of_birth": "1980-05-15T00:00:00Z",
        "diagnosis": "Anxiety disorder",
    }
