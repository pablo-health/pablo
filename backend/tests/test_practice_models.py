# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Practice Mode Pydantic models."""

import pytest
from pydantic import ValidationError

from backend.app.models.practice import (
    CreatePracticeSessionRequest,
    EndPracticeSessionResponse,
    PracticeMode,
    PracticeSessionDetailResponse,
    PracticeSessionListResponse,
    PracticeSessionResponse,
    PracticeTopic,
    PracticeTopicListResponse,
    PracticeTopicResponse,
)


class TestPracticeMode:
    def test_enum_values(self):
        assert PracticeMode.PRACTICE == "practice"
        assert PracticeMode.DEMO == "demo"


class TestCreatePracticeSessionRequest:
    def test_valid_request(self):
        req = CreatePracticeSessionRequest(topic_id="generalized_anxiety")
        assert req.topic_id == "generalized_anxiety"
        assert req.mode == PracticeMode.PRACTICE

    def test_demo_mode(self):
        req = CreatePracticeSessionRequest(topic_id="work_stress", mode=PracticeMode.DEMO)
        assert req.mode == PracticeMode.DEMO

    def test_empty_topic_id_rejected(self):
        with pytest.raises(ValidationError):
            CreatePracticeSessionRequest(topic_id="")


class TestPracticeTopic:
    def test_topic_fields(self):
        topic = PracticeTopic(
            id="test",
            name="Test Topic",
            description="A test topic",
            category="anxiety",
            estimated_duration_minutes=10,
            patient_system_prompt="You are Pablo Bear.",
            therapist_system_prompt="You are a therapist.",
        )
        assert topic.id == "test"
        assert topic.patient_system_prompt == "You are Pablo Bear."


class TestPracticeTopicResponse:
    def test_excludes_system_prompt(self):
        resp = PracticeTopicResponse(
            id="test",
            name="Test",
            description="Desc",
            category="anxiety",
            estimated_duration_minutes=10,
        )
        assert not hasattr(resp, "patient_system_prompt")


class TestPracticeTopicListResponse:
    def test_list_response(self):
        resp = PracticeTopicListResponse(data=[], total=0)
        assert resp.total == 0


class TestPracticeSessionResponse:
    def test_session_response(self):
        resp = PracticeSessionResponse(
            session_id="abc",
            topic_id="test",
            topic_name="Test",
            mode=PracticeMode.PRACTICE,
            status="scheduled",
            ws_url="ws://localhost/ws",
            ws_ticket="test-ticket",
            created_at="2026-03-30T00:00:00Z",
        )
        assert resp.session_id == "abc"
        assert resp.mode == PracticeMode.PRACTICE


class TestPracticeSessionDetailResponse:
    def test_detail_with_no_soap(self):
        resp = PracticeSessionDetailResponse(
            session_id="abc",
            topic_id="test",
            topic_name="Test",
            mode=PracticeMode.PRACTICE,
            status="scheduled",
            created_at="2026-03-30T00:00:00Z",
        )
        assert resp.soap_note is None
        assert resp.duration_seconds is None


class TestPracticeSessionListResponse:
    def test_pagination(self):
        resp = PracticeSessionListResponse(data=[], total=0, page=1, page_size=20)
        assert resp.page == 1


class TestEndPracticeSessionResponse:
    def test_end_response(self):
        resp = EndPracticeSessionResponse(session_id="abc", status="recording_complete")
        assert resp.duration_seconds is None
