"""Service layer for business logic."""

from .audit_service import AuditService, get_audit_service
from .ehr_navigation_service import (
    EhrNavigationService,
    GeminiEhrNavigationService,
    MockEhrNavigationService,
)
from .export_service import ExportService
from .note_generation_service import (
    GeneratedNote,
    MeetingTranscriptionNoteService,
    MockNoteGenerationService,
    NoteGenerationService,
)
from .session_service import (
    InvalidSessionStatusError,
    InvalidStatusTransitionError,
    PatientNotFoundError,
    SessionAlreadyInStatusError,
    SessionInTerminalStatusError,
    SessionNotFoundError,
    SessionService,
    SessionServiceError,
    SOAPGenerationFailedError,
)
from .source_attribution_service import (
    build_attribution_prompt,
    build_claims_from_soap,
    format_transcript_with_segment_ids,
    merge_attribution_into_soap,
    parse_attribution_response,
)

__all__ = [
    "AuditService",
    "EhrNavigationService",
    "ExportService",
    "GeminiEhrNavigationService",
    "GeneratedNote",
    "InvalidSessionStatusError",
    "InvalidStatusTransitionError",
    "MeetingTranscriptionNoteService",
    "MockEhrNavigationService",
    "MockNoteGenerationService",
    "NoteGenerationService",
    "PatientNotFoundError",
    "SOAPGenerationFailedError",
    "SessionAlreadyInStatusError",
    "SessionInTerminalStatusError",
    "SessionNotFoundError",
    "SessionService",
    "SessionServiceError",
    "build_attribution_prompt",
    "build_claims_from_soap",
    "format_transcript_with_segment_ids",
    "get_audit_service",
    "merge_attribution_into_soap",
    "parse_attribution_response",
]
