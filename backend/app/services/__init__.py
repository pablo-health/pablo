"""Service layer for business logic."""

from .audit_service import AuditService, get_audit_service
from .chat_context_bundler import (
    ContextBundle,
    ContextOverflowError,
    InvalidSelectionError,
    assemble_context_bundle,
    default_source_selection,
)
from .chat_llm_gateway import (
    ChatLLMGateway,
    FakeChatLLMGateway,
    GeminiChatLLMGateway,
    StreamEvent,
    UserAssistantTurn,
)
from .chat_model_resolver import (
    ChatModelResolver,
    default_resolve_chat_model,
    get_chat_model_resolver,
)
from .chat_service import ChatConversationNotFoundError, ChatService
from .chat_turn_service import (
    ChatTurnService,
    TurnConcurrencyError,
    TurnContext,
    TurnStreamEvent,
)
from .ehr_navigation_service import (
    EhrNavigationService,
    GeminiEhrNavigationService,
    MockEhrNavigationService,
)
from .export_service import ExportService
from .llm_usage_meter import LlmUsageMeter, period_yyyymm
from .note_generation_service import (
    GeneratedNote,
    MockNoteGenerationService,
    NoteGenerationService,
    RegistryNoteGenerationService,
)
from .note_service import (
    NoteAlreadyFinalizedError,
    NoteNotFinalizedError,
    NoteNotFoundError,
    NoteService,
    NoteServiceError,
)
from .patient_documents_service import (
    ALLOWED_MIME_TYPES as PATIENT_DOCUMENT_ALLOWED_MIME_TYPES,
)
from .patient_documents_service import (
    DocumentExtractionFailedError,
    DocumentsBucketNotConfiguredError,
    FileTooLargeError,
    InitUploadResult,
    PatientDocumentError,
    PatientDocumentsService,
    TransientDocumentExtractionError,
    UnsupportedMimeTypeError,
    UploadNotCompleteError,
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
    TransientSOAPGenerationError,
)
from .source_attribution_service import (
    build_attribution_prompt,
    build_claims_from_soap,
    format_transcript_with_segment_ids,
    merge_attribution_into_soap,
    parse_attribution_response,
)
from .structured_llm_gateway import (
    FakeStructuredLLMGateway,
    GeminiStructuredLLMGateway,
    StructuredCompletion,
    StructuredLLMGateway,
    get_default_structured_llm_gateway,
)

__all__ = [
    "PATIENT_DOCUMENT_ALLOWED_MIME_TYPES",
    "AuditService",
    "ChatConversationNotFoundError",
    "ChatLLMGateway",
    "ChatModelResolver",
    "ChatService",
    "ChatTurnService",
    "ContextBundle",
    "ContextOverflowError",
    "DocumentExtractionFailedError",
    "DocumentsBucketNotConfiguredError",
    "EhrNavigationService",
    "ExportService",
    "FakeChatLLMGateway",
    "FakeStructuredLLMGateway",
    "FileTooLargeError",
    "GeminiChatLLMGateway",
    "GeminiEhrNavigationService",
    "GeminiStructuredLLMGateway",
    "GeneratedNote",
    "InitUploadResult",
    "InvalidSelectionError",
    "InvalidSessionStatusError",
    "InvalidStatusTransitionError",
    "LlmUsageMeter",
    "MockEhrNavigationService",
    "MockNoteGenerationService",
    "NoteAlreadyFinalizedError",
    "NoteGenerationService",
    "NoteNotFinalizedError",
    "NoteNotFoundError",
    "NoteService",
    "NoteServiceError",
    "PatientDocumentError",
    "PatientDocumentsService",
    "PatientNotFoundError",
    "RegistryNoteGenerationService",
    "SOAPGenerationFailedError",
    "SessionAlreadyInStatusError",
    "SessionInTerminalStatusError",
    "SessionNotFoundError",
    "SessionService",
    "SessionServiceError",
    "StreamEvent",
    "StructuredCompletion",
    "StructuredLLMGateway",
    "TransientDocumentExtractionError",
    "TransientSOAPGenerationError",
    "TurnConcurrencyError",
    "TurnContext",
    "TurnStreamEvent",
    "UnsupportedMimeTypeError",
    "UploadNotCompleteError",
    "UserAssistantTurn",
    "assemble_context_bundle",
    "build_attribution_prompt",
    "build_claims_from_soap",
    "default_resolve_chat_model",
    "default_source_selection",
    "format_transcript_with_segment_ids",
    "get_audit_service",
    "get_chat_model_resolver",
    "get_default_structured_llm_gateway",
    "merge_attribution_into_soap",
    "parse_attribution_response",
    "period_yyyymm",
]
