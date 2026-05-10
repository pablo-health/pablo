// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient-context chat primitive — API types.
 *
 * Mirrors backend `app.models.chat`. The chat surface is prompt-neutral:
 * the caller supplies the system prompt and a default source-selection
 * rule at creation; the primitive ships no clinical opinion of its own.
 */

export type ChatRole = "user" | "assistant"

export type ChatSourceSelection = Record<string, unknown>

export interface ChatConversation {
  id: string
  patient_id: string
  owner_user_id: string
  title: string
  caller_feature_key: string
  default_source_selection: ChatSourceSelection
  created_at: string
  last_turn_at: string | null
  archived_at: string | null
}

export interface ChatMessage {
  id: string
  conversation_id: string
  sequence: number
  role: ChatRole
  content: string
  created_at: string
  context_manifest: ChatContextManifest | null
  input_tokens: number | null
  output_tokens: number | null
  llm_model: string | null
  llm_finish_reason: string | null
  llm_error: string | null
}

export interface ChatConversationDetail extends ChatConversation {
  messages: ChatMessage[]
}

export interface ChatConversationListResponse {
  data: ChatConversation[]
  total: number
}

export interface CreateChatConversationRequest {
  patient_id: string
  caller_feature_key: string
  caller_system_prompt: string
  title?: string
  default_source_selection?: ChatSourceSelection
}

export interface UpdateChatConversationRequest {
  title?: string
  default_source_selection?: ChatSourceSelection
  archive?: boolean
}

export interface SendChatMessageRequest {
  content: string
  source_selection?: ChatSourceSelection
}

export interface ChatContextManifestEntry {
  source_key: string
  tokens_est: number
  status?: string
  note_ids?: string[]
  char_count?: number
}

export interface ChatContextManifest {
  sources_included: ChatContextManifestEntry[]
  sources_dropped: { source_key: string; reason: string }[]
  total_tokens_est: number
  token_budget: number
  patient_id: string
  assembled_at: string
}

/**
 * SSE events emitted by `POST /api/chat/conversations/{id}/messages`.
 * Decoded on the client as a discriminated union so React can switch
 * on `kind` without a `switch` over event names.
 */
export type ChatStreamEvent =
  | {
      kind: "meta"
      user_message_id: string
      assistant_message_id: string
      input_tokens: number
      model: string
      sources_dropped?: { source_key: string; reason: string }[]
      quota_remaining_pct?: number
    }
  | { kind: "delta"; text: string }
  | { kind: "done"; output_tokens: number; finish_reason: string }
  | { kind: "error"; error: string; message: string }
