# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Session domain models — TherapySession dataclass and API request/response models.

Sub-modules:
- enums.py: SessionStatus, SOAPSection, ExportStatus, TranscriptFormat
- soap_note.py: SOAPNote, SOAPSentence, structured sub-fields, Pydantic models
- transcript.py: Transcript, TranscriptModel, parsing helpers
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from .enums import (
    ExportStatus,
    SessionSource,
    SessionStatus,
    SessionType,
    SOAPSection,
    TranscriptFormat,
    VideoPlatform,
)
from .soap_note import (
    CONFIDENCE_THRESHOLDS,
    AssessmentNote,
    AssessmentNoteModel,
    ObjectiveNote,
    ObjectiveNoteModel,
    PlanNote,
    PlanNoteModel,
    SOAPNote,
    SOAPNoteModel,
    SOAPSentence,
    SOAPSentenceModel,
    StructuredSOAPNoteModel,
    SubjectiveNote,
    SubjectiveNoteModel,
)
from .soap_note import _to_sentence as _bcompat_to_sentence
from .soap_note import _to_sentence_list as _bcompat_to_sentence_list
from .transcript import (
    Transcript,
    TranscriptModel,
    TranscriptSegmentModel,
    parse_transcript_segments,
)
from .validators import validate_iso_date

# Backward-compatible aliases for old private names used by tests
_to_sentence = _bcompat_to_sentence
_to_sentence_list = _bcompat_to_sentence_list
_parse_transcript_segments = parse_transcript_segments


# --- API request/response models ---


class UploadSessionRequest(BaseModel):
    """Request to upload a session transcript."""

    patient_id: str
    session_date: str
    transcript: TranscriptModel

    @classmethod
    def validate_session_date(cls, v: str) -> str:
        """Validate session_date is ISO format."""
        return validate_iso_date(v, "session_date")  # type: ignore


class FinalizeSessionRequest(BaseModel):
    """Request to finalize a session after therapist review."""

    quality_rating: int = Field(ge=1, le=5)
    quality_rating_reason: str | None = None
    quality_rating_sections: list[SOAPSection] | None = None
    soap_note_edited: SOAPNoteModel | None = None


class UpdateSessionRatingRequest(BaseModel):
    """Request to update session quality rating (for already finalized sessions)."""

    quality_rating: int = Field(ge=1, le=5)
    quality_rating_reason: str | None = None
    quality_rating_sections: list[SOAPSection] | None = None


class ScheduleSessionRequest(BaseModel):
    """Request to create a scheduled session (pre-recording)."""

    patient_id: str
    scheduled_at: str
    duration_minutes: int = Field(default=50, ge=1, le=480)
    video_link: str | None = None
    video_platform: VideoPlatform | None = None
    session_type: SessionType = SessionType.INDIVIDUAL
    source: SessionSource = SessionSource.COMPANION
    notes: str | None = None


class UpdateSessionStatusRequest(BaseModel):
    """Request to transition session status."""

    status: SessionStatus


class UpdateSessionMetadataRequest(BaseModel):
    """Request to update session metadata (reschedule, change video link, etc.)."""

    scheduled_at: str | None = None
    video_link: str | None = None
    video_platform: VideoPlatform | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=480)
    notes: str | None = None


class UploadTranscriptToSessionRequest(BaseModel):
    """Request to upload a transcript to an existing session."""

    format: str
    content: str = Field(min_length=1)


class PatientSummary(BaseModel):
    """Inline patient summary to avoid N+1 fetches."""

    id: str
    first_name: str
    last_name: str


class TodaySessionResponse(BaseModel):
    """Response model for a session in today's day view."""

    id: str
    patient_id: str
    patient: PatientSummary
    status: SessionStatus
    scheduled_at: str | None = None
    duration_minutes: int = 50
    video_link: str | None = None
    video_platform: str | None = None
    session_type: str = "individual"
    source: str = "companion"
    notes: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    created_at: str
    updated_at: str | None = None


class TodaySessionListResponse(BaseModel):
    """Response model for GET /api/sessions/today."""

    data: list[TodaySessionResponse]
    total: int


class SessionResponse(BaseModel):
    """Response model for therapy session."""

    id: str
    user_id: str
    patient_id: str
    patient_name: str
    session_date: str
    session_number: int
    status: SessionStatus
    transcript: TranscriptModel
    created_at: str
    # Companion scheduling fields
    scheduled_at: str | None = None
    video_link: str | None = None
    video_platform: str | None = None
    session_type: str | None = None
    duration_minutes: int | None = None
    source: str | None = None
    notes: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    updated_at: str | None = None
    # Flat narrative SOAP note (for PDF/clipboard backward compat)
    soap_note: SOAPNoteModel | None = None
    soap_note_edited: SOAPNoteModel | None = None
    # Structured SOAP note with source references
    soap_note_structured: StructuredSOAPNoteModel | None = None
    # Parsed transcript segments for source linking
    transcript_segments: list[TranscriptSegmentModel] | None = None
    quality_rating: int | None = None
    quality_rating_reason: str | None = None
    quality_rating_sections: list[str] | None = None
    processing_started_at: str | None = None
    processing_completed_at: str | None = None
    finalized_at: str | None = None
    error: str | None = None
    # PII-redacted versions for review and export
    redacted_transcript: str | None = None
    naturalized_transcript: str | None = None
    redacted_soap_note: SOAPNoteModel | None = None
    naturalized_soap_note: SOAPNoteModel | None = None
    # Export queue tracking
    export_status: str = "not_queued"
    export_queued_at: str | None = None
    export_reviewed_at: str | None = None
    export_reviewed_by: str | None = None
    exported_at: str | None = None

    @staticmethod
    def from_session(session: TherapySession, patient_name: str) -> SessionResponse:
        """Convert TherapySession dataclass to API response model."""
        structured = None
        transcript_segments = None

        if session.soap_note:
            structured = session.soap_note.to_structured_model()
            transcript_segments = parse_transcript_segments(session.transcript.content)

        return SessionResponse(
            id=session.id,
            user_id=session.user_id,
            patient_id=session.patient_id,
            patient_name=patient_name,
            session_date=session.session_date,
            session_number=session.session_number,
            status=SessionStatus(session.status),
            transcript=TranscriptModel(
                format=TranscriptFormat(session.transcript.format),
                content=session.transcript.content,
            ),
            created_at=session.created_at,
            scheduled_at=session.scheduled_at,
            video_link=session.video_link,
            video_platform=session.video_platform,
            session_type=session.session_type,
            duration_minutes=session.duration_minutes,
            source=session.source,
            notes=session.notes,
            started_at=session.started_at,
            ended_at=session.ended_at,
            updated_at=session.updated_at,
            soap_note=session.soap_note.to_narrative_model() if session.soap_note else None,
            soap_note_edited=(
                session.soap_note_edited.to_narrative_model() if session.soap_note_edited else None
            ),
            soap_note_structured=structured,
            transcript_segments=transcript_segments,
            quality_rating=session.quality_rating,
            quality_rating_reason=session.quality_rating_reason,
            quality_rating_sections=session.quality_rating_sections,
            processing_started_at=session.processing_started_at,
            processing_completed_at=session.processing_completed_at,
            finalized_at=session.finalized_at,
            error=session.error,
            redacted_transcript=session.redacted_transcript,
            naturalized_transcript=session.naturalized_transcript,
            redacted_soap_note=(
                session.redacted_soap_note.to_narrative_model()
                if session.redacted_soap_note
                else None
            ),
            naturalized_soap_note=(
                session.naturalized_soap_note.to_narrative_model()
                if session.naturalized_soap_note
                else None
            ),
            export_status=session.export_status,
            export_queued_at=session.export_queued_at,
            export_reviewed_at=session.export_reviewed_at,
            export_reviewed_by=session.export_reviewed_by,
            exported_at=session.exported_at,
        )


class SessionListResponse(BaseModel):
    """Response model for list of therapy sessions."""

    data: list[SessionResponse]
    total: int
    page: int
    page_size: int


# --- TherapySession dataclass ---


@dataclass
class TherapySession:
    """Therapy session data model.

    Represents a therapy session with transcript and SOAP note.
    Status flow: queued -> processing -> pending_review -> finalized (or failed)
    """

    id: str
    user_id: str
    patient_id: str
    session_date: str
    session_number: int
    status: str
    transcript: Transcript
    created_at: str
    # Companion scheduling fields
    scheduled_at: str | None = None
    video_link: str | None = None
    video_platform: str | None = None
    session_type: str | None = None
    duration_minutes: int | None = None
    source: str | None = None
    notes: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    updated_at: str | None = None
    audio_gcs_path: str | None = None
    soap_note: SOAPNote | None = None
    soap_note_edited: SOAPNote | None = None
    quality_rating: int | None = None
    quality_rating_reason: str | None = None
    quality_rating_sections: list[str] | None = None  # SOAP sections needing improvement
    processing_started_at: str | None = None
    processing_completed_at: str | None = None
    finalized_at: str | None = None
    error: str | None = None
    # PII-redacted versions for export
    redacted_transcript: str | None = None  # Transcript with placeholders (<PERSON_1>)
    naturalized_transcript: str | None = None  # Transcript with fake names (for export)
    redacted_soap_note: SOAPNote | None = None  # SOAP note with placeholders
    naturalized_soap_note: SOAPNote | None = None  # SOAP note with fake names (for export)
    # Export queue tracking
    export_status: str = "not_queued"  # ExportStatus enum value
    export_queued_at: str | None = None  # ISO timestamp
    export_reviewed_at: str | None = None  # ISO timestamp
    export_reviewed_by: str | None = None  # User ID who reviewed
    exported_at: str | None = None  # ISO timestamp

    @property
    def was_edited(self) -> bool:
        """Return True if therapist edited the AI-generated note."""
        return self.soap_note_edited is not None

    @property
    def final_soap_note(self) -> SOAPNote | None:
        """Return the final SOAP note (edited if modified, otherwise AI-generated)."""
        return self.soap_note_edited or self.soap_note

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TherapySession:
        """Create TherapySession from Firestore document."""
        transcript_data = data["transcript"]
        transcript = Transcript(
            format=transcript_data["format"], content=transcript_data["content"]
        )

        soap_note = None
        if data.get("soap_note"):
            soap_note = SOAPNote.from_dict(data["soap_note"])

        soap_note_edited = None
        if data.get("soap_note_edited"):
            soap_note_edited = SOAPNote.from_dict(data["soap_note_edited"])

        redacted_soap_note = None
        if data.get("redacted_soap_note"):
            redacted_soap_note = SOAPNote.from_dict(data["redacted_soap_note"])

        naturalized_soap_note = None
        if data.get("naturalized_soap_note"):
            naturalized_soap_note = SOAPNote.from_dict(data["naturalized_soap_note"])

        return cls(
            id=data["id"],
            user_id=data["user_id"],
            patient_id=data["patient_id"],
            session_date=data["session_date"],
            session_number=data["session_number"],
            status=data["status"],
            transcript=transcript,
            created_at=data["created_at"],
            scheduled_at=data.get("scheduled_at"),
            video_link=data.get("video_link"),
            video_platform=data.get("video_platform"),
            session_type=data.get("session_type"),
            duration_minutes=data.get("duration_minutes"),
            source=data.get("source"),
            notes=data.get("notes"),
            started_at=data.get("started_at"),
            ended_at=data.get("ended_at"),
            updated_at=data.get("updated_at"),
            audio_gcs_path=data.get("audio_gcs_path"),
            soap_note=soap_note,
            soap_note_edited=soap_note_edited,
            quality_rating=data.get("quality_rating"),
            quality_rating_reason=data.get("quality_rating_reason"),
            quality_rating_sections=data.get("quality_rating_sections"),
            processing_started_at=data.get("processing_started_at"),
            processing_completed_at=data.get("processing_completed_at"),
            finalized_at=data.get("finalized_at"),
            error=data.get("error"),
            redacted_transcript=data.get("redacted_transcript"),
            naturalized_transcript=data.get("naturalized_transcript"),
            redacted_soap_note=redacted_soap_note,
            naturalized_soap_note=naturalized_soap_note,
            export_status=data.get("export_status", "not_queued"),
            export_queued_at=data.get("export_queued_at"),
            export_reviewed_at=data.get("export_reviewed_at"),
            export_reviewed_by=data.get("export_reviewed_by"),
            exported_at=data.get("exported_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert TherapySession to dictionary for Firestore."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "patient_id": self.patient_id,
            "session_date": self.session_date,
            "session_number": self.session_number,
            "status": self.status,
            "transcript": self.transcript.to_dict(),
            "created_at": self.created_at,
            "scheduled_at": self.scheduled_at,
            "video_link": self.video_link,
            "video_platform": self.video_platform,
            "session_type": self.session_type,
            "duration_minutes": self.duration_minutes,
            "source": self.source,
            "notes": self.notes,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "updated_at": self.updated_at,
            "audio_gcs_path": self.audio_gcs_path,
            "soap_note": self.soap_note.to_dict() if self.soap_note else None,
            "soap_note_edited": self.soap_note_edited.to_dict() if self.soap_note_edited else None,
            "quality_rating": self.quality_rating,
            "quality_rating_reason": self.quality_rating_reason,
            "quality_rating_sections": self.quality_rating_sections,
            "processing_started_at": self.processing_started_at,
            "processing_completed_at": self.processing_completed_at,
            "finalized_at": self.finalized_at,
            "error": self.error,
            "redacted_transcript": self.redacted_transcript,
            "naturalized_transcript": self.naturalized_transcript,
            "redacted_soap_note": (
                self.redacted_soap_note.to_dict() if self.redacted_soap_note else None
            ),
            "naturalized_soap_note": (
                self.naturalized_soap_note.to_dict() if self.naturalized_soap_note else None
            ),
            "export_status": self.export_status,
            "export_queued_at": self.export_queued_at,
            "export_reviewed_at": self.export_reviewed_at,
            "export_reviewed_by": self.export_reviewed_by,
            "exported_at": self.exported_at,
        }


# --- Backward-compatible re-exports ---
# All symbols that were previously importable from app.models.session remain available.
# The unused imports below are intentional re-exports.
__all__ = [
    # soap_note
    "CONFIDENCE_THRESHOLDS",
    "AssessmentNote",
    "AssessmentNoteModel",
    # enums
    "ExportStatus",
    # session (defined here)
    "FinalizeSessionRequest",
    "ObjectiveNote",
    "ObjectiveNoteModel",
    "PatientSummary",
    "PlanNote",
    "PlanNoteModel",
    "SOAPNote",
    "SOAPNoteModel",
    "SOAPSection",
    "SOAPSentence",
    "SOAPSentenceModel",
    "ScheduleSessionRequest",
    "SessionListResponse",
    "SessionResponse",
    "SessionSource",
    "SessionStatus",
    "SessionType",
    "StructuredSOAPNoteModel",
    "SubjectiveNote",
    "SubjectiveNoteModel",
    "TherapySession",
    "TodaySessionListResponse",
    "TodaySessionResponse",
    # transcript
    "Transcript",
    "TranscriptFormat",
    "TranscriptModel",
    "TranscriptSegmentModel",
    "UpdateSessionMetadataRequest",
    "UpdateSessionRatingRequest",
    "UpdateSessionStatusRequest",
    "UploadSessionRequest",
    "UploadTranscriptToSessionRequest",
    "VideoPlatform",
]
