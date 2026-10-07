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
    TRANSCRIBING = "transcribing"
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


_VIDEO_PLATFORM_LABELS: dict[str, VideoPlatform] = {
    "zoom": VideoPlatform.ZOOM,
    "teams": VideoPlatform.TEAMS,
    "microsoft_teams": VideoPlatform.TEAMS,
    "msteams": VideoPlatform.TEAMS,
    "meet": VideoPlatform.MEET,
    "google_meet": VideoPlatform.MEET,
    "googlemeet": VideoPlatform.MEET,
    "hangouts": VideoPlatform.MEET,
    "none": VideoPlatform.NONE,
}


def video_platform_from_label(label: str | None) -> VideoPlatform | None:
    """Read an appointment's free-text platform label as a ``VideoPlatform``.

    An appointment's platform is whatever the scheduling form offered when it
    was booked ("google_meet", "doxy", "other", a calendar import's own name),
    so it is read leniently and never raises. A label with no matching value
    is ``None`` — the platform is unknown — rather than ``NONE``, which would
    claim the visit was not a video call.
    """
    if label is None:
        return None
    return _VIDEO_PLATFORM_LABELS.get(label.strip().lower())


class SessionType(StrEnum):
    """Type of therapy session."""

    INDIVIDUAL = "individual"
    COUPLES = "couples"


class SessionSource(StrEnum):
    """Where the session was created from."""

    WEB = "web"
    COMPANION = "companion"
    CALENDAR = "calendar"
    PRACTICE = "practice"
    # An existing, already-written note imported from a document (PDF/Word/TXT)
    # rather than recorded or transcribed in-app.
    IMPORTED = "imported"


class SOAPSection(StrEnum):
    """SOAP note section identifiers."""

    SUBJECTIVE = "subjective"
    OBJECTIVE = "objective"
    ASSESSMENT = "assessment"
    PLAN = "plan"


class TranscriptFormat(StrEnum):
    """Supported transcript formats."""

    VTT = "vtt"
    JSON = "json"
    TXT = "txt"
    GOOGLE_MEET = "google_meet"


class EhrSystem(StrEnum):
    """Supported EHR systems."""

    SIMPLEPRACTICE = "simplepractice"
    THERAPYNOTES = "therapynotes"
    JANE_APP = "jane_app"
    SESSIONS_HEALTH = "sessions_health"


class EhrAction(StrEnum):
    """EHR navigation actions."""

    CLICK = "click"
    FILL = "fill"
    NAVIGATE = "navigate"
    WAIT = "wait"
    NONE = "none"


class OutcomeMeasureSource(StrEnum):
    """Clinical provenance of a scored outcome measure."""

    PATIENT_SELF_REPORT = "patient_self_report"
    CLINICIAN_ADMINISTERED_VERBAL = "clinician_administered_verbal"
    MANUAL = "manual"
    INFERRED = "inferred"


class PracticeEdition(StrEnum):
    """What kind of operator a practice is, independent of which tables are empty.

    Everything downstream of a practice today assumes ``THERAPIST``:
    patients, appointments, notes, charts. ``PERSONAL`` marks a
    non-clinical operator up front — no patients, no charts, no
    clinical severity floors — so those surfaces can branch on a
    declared fact instead of inferring one from empty tables.

    ``THERAPIST`` is the default: every existing practice and every
    new one that doesn't say otherwise is a clinical practice.
    """

    THERAPIST = "therapist"
    PERSONAL = "personal"


class ClinicianRole(StrEnum):
    """A clinician's relationship to a patient in `patient_clinicians`.

    Pablo intentionally keeps this as a typed enum + DB CHECK constraint
    rather than a separate `app_roles` table: the four values are fixed
    by clinical workflow, the access predicate `app.has_patient_access`
    treats any non-expired row as access-granting, and per-role rules
    (e.g. supervisor read+cosign, primary read+write) live in code
    where they have access to the operation context (note finalized?
    payer enrolled? supervision relationship active?). When/if
    practice-defined custom roles become a requirement, this enum
    becomes a foreign key to a roles table — the read sites already
    go through the typed identifier and migration is mechanical.
    """

    PRIMARY = "primary"
    CO_TREATING = "co_treating"
    SUPERVISOR = "supervisor"
    COVERING = "covering"
