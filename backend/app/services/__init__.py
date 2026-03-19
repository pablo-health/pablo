"""Service layer for business logic."""

from .audit_service import AuditService, get_audit_service
from .export_service import ExportService
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
from .soap_generation_service import (
    MeetingTranscriptionSOAPService,
    MockSOAPGenerationService,
    SOAPGenerationService,
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
    "ExportService",
    "InvalidSessionStatusError",
    "InvalidStatusTransitionError",
    "MeetingTranscriptionSOAPService",
    "MockSOAPGenerationService",
    "PatientNotFoundError",
    "SOAPGenerationFailedError",
    "SOAPGenerationService",
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
