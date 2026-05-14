// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient-context chat — shared frontend types.
 *
 * See ``docs/architecture/patient-context-chat-oss.md`` for the
 * canonical contract. Types here mirror the backend's Pydantic shapes
 * (``backend/app/models/chat_api.py``) and the SSE payloads from the
 * streaming turn service (``backend/app/services/chat_turn_service.py``).
 */

// ---------------------------------------------------------------------------
// Source selection (§7 of design doc)
// ---------------------------------------------------------------------------

export const SOURCE_KEYS = [
  "pasted_text",
  "current_medications",
  "most_recent_intake",
  "progress_notes_recent",
  "progress_notes_explicit",
  "treatment_plan_active",
  "safety_plan_active",
  "lab_values_recent",
  "vitals_recent",
] as const

export type SourceKey = (typeof SOURCE_KEYS)[number]

/**
 * Per-source params. Most sources accept ``true`` to opt in with
 * defaults; the dict form carries source-specific tuning.
 */
export type SourceParams =
  | true
  | {
      content?: string // pasted_text
      limit?: number // progress_notes_recent, lab_values_recent, vitals_recent
      include_transcripts?: boolean // progress_notes_recent
      note_ids?: string[] // progress_notes_explicit
    }

export type SourceSelection = Partial<Record<SourceKey, SourceParams>>

/**
 * Source family — drives the chip's color band per §13.2 of the design doc.
 */
export type SourceFamily = "sessions" | "documents" | "manual"

// ---------------------------------------------------------------------------
// Manifest (§7.5)
// ---------------------------------------------------------------------------

export interface ManifestIncludedEntry {
  source_key: SourceKey
  tokens_est: number
  row_count?: number
  note_ids?: string[]
  chars?: number
  rows_dropped?: number
  dropped_note_ids?: string[]
  reason?: string
}

export interface ManifestDroppedEntry {
  source_key: SourceKey
  reason: string
}

export interface ContextManifest {
  sources_included: ManifestIncludedEntry[]
  sources_dropped: ManifestDroppedEntry[]
  total_tokens_est: number
  token_budget: number
  patient_id: string
  assembled_at: string
}

// ---------------------------------------------------------------------------
// Conversation + message (§3, §4)
// ---------------------------------------------------------------------------

export type ChatMessageRole = "user" | "assistant"

export interface ChatMessage {
  id: string
  conversation_id: string
  sequence: number
  role: ChatMessageRole
  content: string
  created_at: string
  source_selection: SourceSelection | null
  context_manifest: ContextManifest | null
  input_tokens: number | null
  output_tokens: number | null
  llm_model: string | null
  llm_finish_reason: string | null
  llm_error: string | null
}

export interface ChatConversation {
  id: string
  patient_id: string
  owner_user_id: string
  title: string
  caller_feature_key: string
  default_source_selection: SourceSelection | null
  created_at: string
  last_turn_at: string | null
  archived_at: string | null
}

export interface ChatConversationDetail extends ChatConversation {
  messages: ChatMessage[]
}

// ---------------------------------------------------------------------------
// Request shapes (§4.1)
// ---------------------------------------------------------------------------

export interface CreateChatConversationRequest {
  patient_id: string
  caller_feature_key: string
  caller_system_prompt: string
  title?: string
  default_source_selection?: SourceSelection
}

export interface UpdateChatConversationRequest {
  title?: string
  default_source_selection?: SourceSelection
  archive?: boolean
}

export interface SendChatMessageRequest {
  content: string
  source_selection?: SourceSelection
  model?: string
}

// ---------------------------------------------------------------------------
// SSE events (§8.1)
// ---------------------------------------------------------------------------

export interface ChatStreamMetaEvent {
  user_message_id: string
  assistant_message_id: string
  input_tokens: number
  model: string
  manifest: ContextManifest
  /** Optional Phase-3b quota disclosure. */
  quota_status?: Record<string, unknown>
}

export interface ChatStreamDeltaEvent {
  text: string
}

export interface ChatStreamDoneEvent {
  output_tokens: number | null
  finish_reason: "stop" | "length" | "safety" | "error"
}

/**
 * Every value that can appear in ``error.error`` per §8.2. Kept as a
 * union so callers can switch exhaustively.
 */
export type ChatErrorCode =
  | "empty_message"
  | "message_too_long"
  | "context_too_large"
  | "invalid_selection"
  | "safety_block"
  | "quota_exceeded"
  | "timeout"
  | "service_unavailable"
  | "auth_denied"
  | "llm_error"
  | "concurrent_turn"

export interface ChatStreamErrorEvent {
  error: ChatErrorCode | string
  message: string
}

// ---------------------------------------------------------------------------
// SSE consumer callbacks (§13.12)
// ---------------------------------------------------------------------------

export interface ChatStreamCallbacks {
  onMeta: (event: ChatStreamMetaEvent) => void
  onDelta: (event: ChatStreamDeltaEvent) => void
  onDone: (event: ChatStreamDoneEvent) => void
  onError: (event: ChatStreamErrorEvent) => void
}
