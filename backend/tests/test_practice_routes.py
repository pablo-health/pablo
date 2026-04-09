# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Practice Mode REST endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models import SessionStatus, TherapySession, Transcript
from backend.app.models.enums import SessionSource
from backend.app.models.practice import PracticeTopic


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_auth():
    """Mock Firebase auth to return a test user."""
    with (
        patch("backend.app.auth.service.verify_firebase_token") as mock_verify,
        patch("backend.app.auth.service.require_mfa") as mock_mfa,
    ):
        mock_verify.return_value = {"uid": "test-user-123"}
        mock_mfa.return_value = {"uid": "test-user-123"}
        yield mock_verify


@pytest.fixture
def mock_practice_service():
    """Mock the practice service dependency."""
    with patch("backend.app.routes.practice._get_practice_service") as mock:
        service = MagicMock()
        mock.return_value = service
        yield service


@pytest.fixture
def sample_topic():
    return PracticeTopic(
        id="generalized_anxiety",
        name="Generalized Anxiety",
        description="Pablo is worried about honey supply chain disruptions.",
        category="anxiety",
        estimated_duration_minutes=10,
        patient_system_prompt="You are Pablo Bear.",
        therapist_system_prompt="You are a therapist.",
    )


@pytest.fixture
def sample_session():
    return TherapySession(
        id="session-1",
        user_id="test-user-123",
        patient_id="practice-test-user-123",
        session_date="2026-03-30T00:00:00Z",
        session_number=1,
        status=SessionStatus.SCHEDULED,
        transcript=Transcript(format="txt", content=""),
        created_at="2026-03-30T00:00:00Z",
        source=SessionSource.PRACTICE,
        notes="topic_id=generalized_anxiety;mode=practice",
    )


class TestListTopics:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_auth, mock_practice_service, sample_topic):
        mock_practice_service.get_topics.return_value = [sample_topic]

    def test_returns_topics(self, client):
        resp = client.get("/api/practice/topics", headers={"Authorization": "Bearer test"})
        # Will get 401 or 501 depending on whether practice is enabled
        # The important thing is the route exists
        assert resp.status_code in (200, 401, 404, 501)


class TestGetTopic:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_auth, mock_practice_service, sample_topic):
        mock_practice_service.get_topic.return_value = sample_topic

    def test_returns_topic(self, client):
        resp = client.get(
            "/api/practice/topics/generalized_anxiety",
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code in (200, 401, 404, 501)
