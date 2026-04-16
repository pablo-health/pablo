# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for PracticeService — topic catalog, session lifecycle, rate limiting."""

from datetime import UTC, datetime

import pytest

from backend.app.models import SessionStatus, TherapySession, Transcript
from backend.app.models.enums import SessionSource
from backend.app.models.practice import CreatePracticeSessionRequest, PracticeMode
from backend.app.repositories import InMemoryPatientRepository, InMemoryTherapySessionRepository
from backend.app.services.practice_service import (
    PracticeConcurrentLimitError,
    PracticeDailyLimitError,
    PracticeService,
    PracticeSessionNotEndableError,
    PracticeSessionNotFoundError,
    PracticeTopicNotFoundError,
)
from backend.app.settings import Settings


@pytest.fixture
def settings():
    return Settings(
        practice_enabled=True,
        practice_daily_session_limit=3,
        practice_max_concurrent=1,
        environment="development",
    )


@pytest.fixture
def session_repo():
    return InMemoryTherapySessionRepository()


@pytest.fixture
def patient_repo():
    return InMemoryPatientRepository()


@pytest.fixture
def service(session_repo, patient_repo, settings):
    return PracticeService(session_repo, patient_repo, settings)


class TestTopicCatalog:
    def test_get_topics_returns_six(self, service):
        topics = service.get_topics()
        assert len(topics) == 6

    def test_get_topic_by_id(self, service):
        topic = service.get_topic("generalized_anxiety")
        assert topic is not None
        assert topic.name == "Generalized Anxiety"
        assert topic.category == "anxiety"

    def test_get_topic_not_found(self, service):
        assert service.get_topic("nonexistent") is None

    def test_topics_have_system_prompts(self, service):
        for topic in service.get_topics():
            assert topic.patient_system_prompt
            assert topic.therapist_system_prompt
            assert "Pablo Bear" in topic.patient_system_prompt


class TestCreateSession:
    def test_creates_session_and_patient(self, service, session_repo, patient_repo):
        request = CreatePracticeSessionRequest(topic_id="generalized_anxiety")
        session, topic = service.create_session("user-1", request)

        assert session.status == SessionStatus.SCHEDULED
        assert session.source == SessionSource.PRACTICE
        assert topic.id == "generalized_anxiety"
        assert "topic_id=generalized_anxiety" in session.notes

        # Pablo Bear patient was auto-created
        patient = patient_repo.get("practice-user-1", "user-1")
        assert patient is not None
        assert patient.first_name == "Pablo Practice"
        assert patient.last_name == "Bear"

    def test_idempotent_patient_creation(self, service, patient_repo):
        req = CreatePracticeSessionRequest(topic_id="generalized_anxiety")
        session1, _ = service.create_session("user-1", req)
        service.end_session(session1.id, "user-1")  # End first to avoid concurrent limit
        service.create_session("user-1", req)

        # Should still be just one Pablo Bear patient
        patient = patient_repo.get("practice-user-1", "user-1")
        assert patient is not None

    def test_demo_mode_stored_in_notes(self, service):
        req = CreatePracticeSessionRequest(topic_id="work_stress", mode=PracticeMode.DEMO)
        session, _ = service.create_session("user-1", req)
        assert "mode=demo" in session.notes

    def test_invalid_topic_raises(self, service):
        req = CreatePracticeSessionRequest(topic_id="nonexistent")
        with pytest.raises(PracticeTopicNotFoundError):
            service.create_session("user-1", req)

    def test_daily_limit_enforced(self, service, settings):
        req = CreatePracticeSessionRequest(topic_id="generalized_anxiety")
        for _ in range(settings.practice_daily_session_limit):
            session, _ = service.create_session("user-1", req)
            # End session so concurrent limit doesn't block
            service.end_session(session.id, "user-1")

        with pytest.raises(PracticeDailyLimitError):
            service.create_session("user-1", req)

    def test_concurrent_limit_enforced(self, service):
        req = CreatePracticeSessionRequest(topic_id="generalized_anxiety")
        service.create_session("user-1", req)
        # Second session while first is still scheduled
        with pytest.raises(PracticeConcurrentLimitError):
            service.create_session("user-1", req)


class TestListSessions:
    def test_list_empty(self, service):
        sessions, total = service.list_sessions("user-1")
        assert total == 0
        assert sessions == []

    def test_list_returns_practice_only(self, service, session_repo):
        req = CreatePracticeSessionRequest(topic_id="generalized_anxiety")
        service.create_session("user-1", req)

        # Create a non-practice session in the same repo
        non_practice = TherapySession(
            id="non-practice",
            user_id="user-1",
            patient_id="practice-user-1",
            session_date=datetime(2026, 3, 30, tzinfo=UTC),
            session_number=1,
            status="queued",
            transcript=Transcript(format="txt", content=""),
            created_at=datetime(2026, 3, 30, tzinfo=UTC),
            source="web",
        )
        session_repo.create(non_practice)

        sessions, total = service.list_sessions("user-1")
        assert total == 1
        assert sessions[0].source == SessionSource.PRACTICE


class TestEndSession:
    def test_end_scheduled_session(self, service):
        req = CreatePracticeSessionRequest(topic_id="generalized_anxiety")
        session, _ = service.create_session("user-1", req)

        ended = service.end_session(session.id, "user-1")
        assert ended.status == SessionStatus.RECORDING_COMPLETE
        assert ended.ended_at is not None

    def test_end_in_progress_session(self, service):
        req = CreatePracticeSessionRequest(topic_id="generalized_anxiety")
        session, _ = service.create_session("user-1", req)
        service.start_session(session.id, "user-1")

        ended = service.end_session(session.id, "user-1")
        assert ended.status == SessionStatus.RECORDING_COMPLETE

    def test_end_nonexistent_raises(self, service):
        with pytest.raises(PracticeSessionNotFoundError):
            service.end_session("nonexistent", "user-1")

    def test_end_already_ended_raises(self, service):
        req = CreatePracticeSessionRequest(topic_id="generalized_anxiety")
        session, _ = service.create_session("user-1", req)
        service.end_session(session.id, "user-1")

        with pytest.raises(PracticeSessionNotEndableError):
            service.end_session(session.id, "user-1")


class TestStartSession:
    def test_start_transitions_to_in_progress(self, service):
        req = CreatePracticeSessionRequest(topic_id="generalized_anxiety")
        session, _ = service.create_session("user-1", req)

        started = service.start_session(session.id, "user-1")
        assert started.status == SessionStatus.IN_PROGRESS
        assert started.started_at is not None

    def test_start_nonexistent_raises(self, service):
        with pytest.raises(PracticeSessionNotFoundError):
            service.start_session("nonexistent", "user-1")
