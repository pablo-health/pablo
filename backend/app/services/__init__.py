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
from .note_generation_service import (
    GeneratedNote,
    MeetingTranscriptionNoteService,
    MockNoteGenerationService,
    NoteGenerationService,
)
from .note_service import (
    NoteAlreadyFinalizedError,
    NoteNotFinalizedError,
    NoteNotFoundError,
    NoteService,
    NoteServiceError,
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
    "ChatConversationNotFoundError",
    "ChatLLMGateway",
    "ChatModelResolver",
    "ChatService",
    "ChatTurnService",
    "ContextBundle",
    "ContextOverflowError",
    "EhrNavigationService",
    "ExportService",
    "FakeChatLLMGateway",
    "GeminiChatLLMGateway",
    "GeminiEhrNavigationService",
    "GeneratedNote",
    "InvalidSelectionError",
    "InvalidSessionStatusError",
    "InvalidStatusTransitionError",
    "MeetingTranscriptionNoteService",
    "MockEhrNavigationService",
    "MockNoteGenerationService",
    "NoteAlreadyFinalizedError",
    "NoteGenerationService",
    "NoteNotFinalizedError",
    "NoteNotFoundError",
    "NoteService",
    "NoteServiceError",
    "PatientNotFoundError",
    "SOAPGenerationFailedError",
    "SessionAlreadyInStatusError",
    "SessionInTerminalStatusError",
    "SessionNotFoundError",
    "SessionService",
    "SessionServiceError",
    "StreamEvent",
    "TurnConcurrencyError",
    "TurnContext",
    "TurnStreamEvent",
    "UserAssistantTurn",
    "assemble_context_bundle",
    "build_attribution_prompt",
    "build_claims_from_soap",
    "default_resolve_chat_model",
    "default_source_selection",
    "format_transcript_with_segment_ids",
    "get_audit_service",
    "get_chat_model_resolver",
    "merge_attribution_into_soap",
    "parse_attribution_response",
]
