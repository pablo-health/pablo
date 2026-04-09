# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice Mode models — request/response schemas and domain types."""

from enum import StrEnum

from pydantic import BaseModel, Field

from .soap_note import SOAPNoteModel


class PracticeMode(StrEnum):
    """Practice session mode."""

    PRACTICE = "practice"  # Human therapist + AI patient
    DEMO = "demo"  # AI therapist + AI patient (fully automated)


# --- Domain types (not directly exposed in API) ---


class PracticeTopic(BaseModel):
    """A practice topic loaded from static JSON."""

    id: str
    name: str
    description: str
    category: str
    estimated_duration_minutes: int
    patient_system_prompt: str
    therapist_system_prompt: str


# --- API request models ---


class CreatePracticeSessionRequest(BaseModel):
    """Request to create a practice session."""

    topic_id: str = Field(min_length=1, max_length=100)
    mode: PracticeMode = PracticeMode.PRACTICE


# --- API response models ---


class PracticeTopicResponse(BaseModel):
    """Public topic representation (no system prompts)."""

    id: str
    name: str
    description: str
    category: str
    estimated_duration_minutes: int


class PracticeTopicListResponse(BaseModel):
    data: list[PracticeTopicResponse]
    total: int


class PracticeSessionResponse(BaseModel):
    """Response after creating a practice session."""

    session_id: str
    topic_id: str
    topic_name: str
    mode: PracticeMode
    status: str
    ws_url: str
    ws_ticket: str
    created_at: str


class PracticeSessionDetailResponse(BaseModel):
    """Full detail for a practice session."""

    session_id: str
    topic_id: str
    topic_name: str
    mode: PracticeMode
    status: str
    duration_seconds: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    created_at: str
    soap_note: SOAPNoteModel | None = None


class PracticeSessionListItem(BaseModel):
    session_id: str
    topic_id: str
    topic_name: str
    mode: PracticeMode
    status: str
    duration_seconds: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    created_at: str
    has_soap_note: bool = False


class PracticeSessionListResponse(BaseModel):
    data: list[PracticeSessionListItem]
    total: int
    page: int
    page_size: int


class EndPracticeSessionResponse(BaseModel):
    session_id: str
    status: str
    duration_seconds: int | None = None
