# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Session domain models — TherapySession dataclass and API request/response models.

Sub-modules:
- enums.py: SessionStatus, SOAPSection, ExportStatus, TranscriptFormat
- soap_note.py: SOAPNote, SOAPSentence, structured sub-fields, Pydantic models
- transcript.py: Transcript, TranscriptModel, parsing helpers
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
from .transcript import (
    Transcript,
    TranscriptModel,
    TranscriptSegmentModel,
    parse_transcript_segments,
)
from .validators import validate_iso_date

# --- API request/response models ---


class UploadSessionRequest(BaseModel):
    """Request to upload a session transcript."""

    patient_id: str
    session_date: datetime
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
    scheduled_at: datetime
    duration_minutes: int = Field(default=50, ge=1, le=480)
    video_link: str | None = None
    video_platform: VideoPlatform | None = None
    session_type: SessionType = SessionType.INDIVIDUAL
    source: SessionSource = SessionSource.COMPANION
    notes: str | None = None
    note_type: str | None = Field(
        default=None,
        description="Note-type registry key (e.g. 'soap', 'narrative'). Defaults to 'soap'.",
    )


class UpdateSessionStatusRequest(BaseModel):
    """Request to transition session status."""

    status: SessionStatus


class UpdateSessionMetadataRequest(BaseModel):
    """Request to update session metadata (reschedule, change video link, etc.)."""

    scheduled_at: datetime | None = None
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
    scheduled_at: datetime | None = None
    duration_minutes: int = 50
    video_link: str | None = None
    video_platform: str | None = None
    session_type: str = "individual"
    source: str = "companion"
    notes: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class TodaySessionListResponse(BaseModel):
    """Response model for GET /api/sessions/today."""

    data: list[TodaySessionResponse]
    total: int


class SessionResponse(BaseModel):
    """Response model for therapy session.

    Note content (SOAP body, edits, quality, export status) lives under
    ``note`` — see :class:`app.models.notes.NoteResponse`. Frontend should
    read ``response.note.*`` rather than the legacy flat fields.
    """

    id: str
    user_id: str
    patient_id: str
    patient_name: str
    session_date: datetime
    session_number: int
    status: SessionStatus
    transcript: TranscriptModel
    created_at: datetime
    # Companion scheduling fields
    scheduled_at: datetime | None = None
    video_link: str | None = None
    video_platform: str | None = None
    session_type: str | None = None
    duration_minutes: int | None = None
    source: str | None = None
    notes: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    updated_at: datetime | None = None
    # Parsed transcript segments for source linking
    transcript_segments: list[TranscriptSegmentModel] | None = None
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None
    error: str | None = None
    # PII-redacted transcript variants
    redacted_transcript: str | None = None
    naturalized_transcript: str | None = None
    # Embedded note (None when this session has no generated note yet).
    note: NoteResponse | None = None

    @staticmethod
    def from_session(
        session: TherapySession,
        patient_name: str,
        note: NoteResponse | None = None,
    ) -> SessionResponse:
        """Convert TherapySession dataclass to API response model.

        ``note`` is the embedded :class:`NoteResponse` for this session, if
        a note has been generated. Callers fetch the note via
        ``NotesRepository.get_by_session_id`` and pass it through.
        """
        transcript_segments = None
        if note is not None and note.content is not None:
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
            transcript_segments=transcript_segments,
            processing_started_at=session.processing_started_at,
            processing_completed_at=session.processing_completed_at,
            error=session.error,
            redacted_transcript=session.redacted_transcript,
            naturalized_transcript=session.naturalized_transcript,
            note=note,
        )


class SessionListResponse(BaseModel):
    """Response model for list of therapy sessions."""

    data: list[SessionResponse]
    total: int
    page: int
    page_size: int


# Resolve SessionResponse's forward ref to NoteResponse. The local import
# avoids a circular import at module load: notes.py imports nothing from
# this module, but session.py is imported before notes.py from
# ``app.models.__init__``, so we cannot import NoteResponse at the top.
from .notes import NoteResponse  # noqa: E402, TC001

SessionResponse.model_rebuild()


# --- TherapySession dataclass ---


@dataclass
class TherapySession:
    """Therapy session (recording-only) data model.

    Represents the recording side of a therapy encounter — transcript,
    audio, status. Note content lives on :class:`app.models.note.Note`
    (see pa-0nx). At most one note exists per session, joined via
    ``Note.session_id``.

    Status flow: queued -> processing -> pending_review -> finalized (or failed)
    """

    id: str
    user_id: str
    patient_id: str
    session_date: datetime
    session_number: int
    status: str
    transcript: Transcript
    created_at: datetime
    # Companion scheduling fields
    scheduled_at: datetime | None = None
    video_link: str | None = None
    video_platform: str | None = None
    session_type: str | None = None
    duration_minutes: int | None = None
    source: str | None = None
    notes: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    updated_at: datetime | None = None
    audio_gcs_path: str | None = None
    transcription_job_metadata: dict[str, Any] | None = None
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None
    error: str | None = None
    # PII-redacted transcript variants (note-side variants live on Note).
    redacted_transcript: str | None = None
    naturalized_transcript: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TherapySession:
        """Create TherapySession from dictionary."""
        transcript_data = data["transcript"]
        transcript = Transcript(
            format=transcript_data["format"], content=transcript_data["content"]
        )

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
            transcription_job_metadata=data.get("transcription_job_metadata"),
            processing_started_at=data.get("processing_started_at"),
            processing_completed_at=data.get("processing_completed_at"),
            error=data.get("error"),
            redacted_transcript=data.get("redacted_transcript"),
            naturalized_transcript=data.get("naturalized_transcript"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert TherapySession to dictionary."""
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
            "transcription_job_metadata": self.transcription_job_metadata,
            "processing_started_at": self.processing_started_at,
            "processing_completed_at": self.processing_completed_at,
            "error": self.error,
            "redacted_transcript": self.redacted_transcript,
            "naturalized_transcript": self.naturalized_transcript,
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
