"""
Data models for Pablo.

Models are organized by domain:
- audit.py: Audit logging for HIPAA compliance
- patient.py: Patient management
- scheduling.py: Appointment scheduling API models
- session.py: Therapy sessions, SOAP notes, and API models
  - enums.py: Session-related enumerations
  - soap_note.py: SOAP note dataclasses and Pydantic models
  - transcript.py: Transcript models and parsing helpers
- user.py: Therapist/clinician users
"""

from .audit import AuditAction, AuditLogEntry, ResourceType
from .patient import (
    CreatePatientRequest,
    DeletePatientResponse,
    ExportFormat,
    Patient,
    PatientExportData,
    PatientListResponse,
    PatientResponse,
    UpdatePatientRequest,
)
from .scheduling import (
    AppointmentListResponse,
    AppointmentResponse,
    AvailabilityRuleListResponse,
    AvailabilityRuleResponse,
    CheckConflictsRequest,
    CheckConflictsResponse,
    ConflictResponse,
    CreateAppointmentRequest,
    CreateAvailabilityRuleRequest,
    CreateRecurringAppointmentRequest,
    EditSeriesRequest,
    FreeSlotsResponse,
    TimeSlotResponse,
    UpdateAppointmentRequest,
    UpdateAvailabilityRuleRequest,
)
from .session import (
    CONFIDENCE_THRESHOLDS,
    AssessmentNote,
    FinalizeSessionRequest,
    ObjectiveNote,
    PatientSummary,
    PlanNote,
    ScheduleSessionRequest,
    SessionListResponse,
    SessionResponse,
    SessionSource,
    SessionStatus,
    SessionType,
    SOAPNote,
    SOAPNoteModel,
    SOAPSection,
    SOAPSentence,
    SOAPSentenceModel,
    StructuredSOAPNoteModel,
    SubjectiveNote,
    TherapySession,
    TodaySessionListResponse,
    TodaySessionResponse,
    Transcript,
    TranscriptFormat,
    TranscriptModel,
    TranscriptSegmentModel,
    UpdateSessionMetadataRequest,
    UpdateSessionRatingRequest,
    UpdateSessionStatusRequest,
    UploadSessionRequest,
    UploadTranscriptToSessionRequest,
    VideoPlatform,
)
from .user import AcceptBAARequest, BAAStatusResponse, UpdateUserRequest, User, UserPreferences

__all__ = [
    # Session models
    "CONFIDENCE_THRESHOLDS",
    # User models
    "AcceptBAARequest",
    # Scheduling models
    "AppointmentListResponse",
    "AppointmentResponse",
    "AssessmentNote",
    # Audit models
    "AuditAction",
    "AuditLogEntry",
    "AvailabilityRuleListResponse",
    "AvailabilityRuleResponse",
    "BAAStatusResponse",
    "CheckConflictsRequest",
    "CheckConflictsResponse",
    "ConflictResponse",
    # Patient models
    "CreateAppointmentRequest",
    "CreateAvailabilityRuleRequest",
    "CreatePatientRequest",
    "CreateRecurringAppointmentRequest",
    "DeletePatientResponse",
    "EditSeriesRequest",
    "ExportFormat",
    "FinalizeSessionRequest",
    "FreeSlotsResponse",
    "ObjectiveNote",
    "Patient",
    "PatientExportData",
    "PatientListResponse",
    "PatientResponse",
    "PatientSummary",
    "PlanNote",
    "ResourceType",
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
    "TherapySession",
    "TimeSlotResponse",
    "TodaySessionListResponse",
    "TodaySessionResponse",
    "Transcript",
    "TranscriptFormat",
    "TranscriptModel",
    "TranscriptSegmentModel",
    "UpdateAppointmentRequest",
    "UpdateAvailabilityRuleRequest",
    "UpdatePatientRequest",
    "UpdateSessionMetadataRequest",
    "UpdateSessionRatingRequest",
    "UpdateSessionStatusRequest",
    "UpdateUserRequest",
    "UploadSessionRequest",
    "UploadTranscriptToSessionRequest",
    "User",
    "UserPreferences",
    "VideoPlatform",
]
