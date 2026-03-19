"""Shared fixtures for integration tests.

Provides Firestore emulator setup and other integration test utilities.
"""

import os
from typing import Any

import pytest
from app.database import get_firestore_client


@pytest.fixture(scope="session", autouse=True)
def _firestore_emulator() -> None:
    """
    Ensure Firestore emulator is configured for all integration tests.

    This fixture doesn't start the emulator - it must be running externally.
    It verifies the FIRESTORE_EMULATOR_HOST environment variable is set.
    """
    if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        pytest.skip(
            "Firestore emulator not available. "
            "Set FIRESTORE_EMULATOR_HOST=localhost:8080 and start emulator with: "
            "firebase emulators:start --only firestore"
        )


@pytest.fixture
def firestore_client() -> Any:
    """
    Get Firestore client connected to emulator.

    Returns a fresh client instance for each test.
    """
    # Clear the cache to ensure we get a fresh client
    get_firestore_client.cache_clear()
    return get_firestore_client()


@pytest.fixture
def clean_firestore(firestore_client: Any) -> Any:
    """
    Provide a clean Firestore instance for each test.

    Deletes all collections before and after the test runs.
    """

    def _cleanup() -> None:
        """Delete all documents in all collections."""
        collections = firestore_client.collections()
        for collection in collections:
            docs = collection.stream()
            for doc in docs:
                doc.reference.delete()

    # Clean before test
    _cleanup()

    yield firestore_client

    # Clean after test
    _cleanup()


@pytest.fixture
def test_user_id() -> str:
    """Default test user ID for integration tests."""
    return "integration-test-user-123"


@pytest.fixture
def test_user_id_2() -> str:
    """Second test user ID for multi-tenant tests."""
    return "integration-test-user-456"
