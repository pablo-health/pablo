# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Session-related enumerations."""

from enum import StrEnum


class SessionStatus(StrEnum):
    """Session processing status."""

    # Companion lifecycle statuses
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    RECORDING_COMPLETE = "recording_complete"
    CANCELLED = "cancelled"
    # Existing SOAP pipeline statuses
    QUEUED = "queued"
    PROCESSING = "processing"
    PENDING_REVIEW = "pending_review"
    FINALIZED = "finalized"
    FAILED = "failed"


class VideoPlatform(StrEnum):
    """Supported video call platforms."""

    ZOOM = "zoom"
    TEAMS = "teams"
    MEET = "meet"
    NONE = "none"


class SessionType(StrEnum):
    """Type of therapy session."""

    INDIVIDUAL = "individual"
    COUPLES = "couples"


class SessionSource(StrEnum):
    """Where the session was created from."""

    WEB = "web"
    COMPANION = "companion"
    CALENDAR = "calendar"


class SOAPSection(StrEnum):
    """SOAP note section identifiers."""

    SUBJECTIVE = "subjective"
    OBJECTIVE = "objective"
    ASSESSMENT = "assessment"
    PLAN = "plan"


class ExportStatus(StrEnum):
    """Export queue status for eval sessions."""

    NOT_QUEUED = "not_queued"  # Default - not selected for export
    PENDING_REVIEW = "pending_review"  # Queued, awaiting manual review
    APPROVED = "approved"  # Reviewed and approved for export
    EXPORTED = "exported"  # Successfully exported
    SKIPPED = "skipped"  # Redaction failed or manually skipped


class TranscriptFormat(StrEnum):
    """Supported transcript formats."""

    VTT = "vtt"
    JSON = "json"
    TXT = "txt"
    GOOGLE_MEET = "google_meet"
