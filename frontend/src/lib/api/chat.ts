// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient-context chat primitive — REST + SSE client.
 *
 * Mirrors `/api/chat/*`. The streaming `sendChatMessage` helper opens
 * a `fetch` stream against the backend, parses the SSE event stream
 * (event/data lines), and yields decoded `ChatStreamEvent` objects so
 * the UI can append tokens as they arrive.
 */

import type {
  ChatConversation,
  ChatConversationDetail,
  ChatConversationListResponse,
  ChatStreamEvent,
  CreateChatConversationRequest,
  SendChatMessageRequest,
  UpdateChatConversationRequest,
} from "@/types/chat"
import { getFirebaseAuth } from "@/lib/firebase"
import { apiClient } from "./client"

let runtimeApiUrl = "http://localhost:8000"

export function setChatApiUrl(url: string): void {
  runtimeApiUrl = url
}

function chatApiUrl(): string {
  if (typeof window === "undefined") {
    return process.env.API_URL || "http://localhost:8000"
  }
  return runtimeApiUrl
}

export async function createChatConversation(
  data: CreateChatConversationRequest,
  token?: string,
): Promise<ChatConversation> {
  return apiClient<ChatConversation>("/api/chat/conversations", {
    method: "POST",
    body: JSON.stringify(data),
    token,
  })
}

export async function listChatConversations(
  patientId: string,
  options: {
    callerFeatureKey?: string
    includeArchived?: boolean
    limit?: number
    offset?: number
    token?: string
  } = {},
): Promise<ChatConversationListResponse> {
  const params = new URLSearchParams({ patient_id: patientId })
  if (options.callerFeatureKey) {
    params.set("caller_feature_key", options.callerFeatureKey)
  }
  if (options.includeArchived) {
    params.set("include_archived", "true")
  }
  if (options.limit !== undefined) {
    params.set("limit", String(options.limit))
  }
  if (options.offset !== undefined) {
    params.set("offset", String(options.offset))
  }
  return apiClient<ChatConversationListResponse>(
    `/api/chat/conversations?${params.toString()}`,
    { method: "GET", token: options.token },
  )
}

export async function fetchChatConversation(
  conversationId: string,
  token?: string,
): Promise<ChatConversationDetail> {
  return apiClient<ChatConversationDetail>(
    `/api/chat/conversations/${conversationId}`,
    { method: "GET", token },
  )
}

export async function updateChatConversation(
  conversationId: string,
  data: UpdateChatConversationRequest,
  token?: string,
): Promise<ChatConversation> {
  return apiClient<ChatConversation>(
    `/api/chat/conversations/${conversationId}`,
    { method: "PATCH", body: JSON.stringify(data), token },
  )
}

export async function deleteChatConversation(
  conversationId: string,
  options: { mode?: "purge" | "archive"; token?: string } = {},
): Promise<void> {
  const params = new URLSearchParams()
  if (options.mode) params.set("mode", options.mode)
  await apiClient<void>(
    `/api/chat/conversations/${conversationId}?${params.toString()}`,
    { method: "DELETE", token: options.token },
  )
}

/**
 * Stream a single chat turn. The async iterator yields
 * `ChatStreamEvent` records as the server emits them; iteration ends
 * when the server closes the connection.
 *
 * Cancellation: pass an `AbortSignal` to abort the underlying fetch.
 * The server-side turn still finalizes (it persists what it has) so
 * an aborted client doesn't leak a half-written assistant row.
 */
export async function* sendChatMessage(
  conversationId: string,
  body: SendChatMessageRequest,
  options: { signal?: AbortSignal; token?: string } = {},
): AsyncGenerator<ChatStreamEvent, void, void> {
  let authToken = options.token
  if (!authToken && typeof window !== "undefined") {
    try {
      const currentUser = getFirebaseAuth().currentUser
      if (currentUser) authToken = await currentUser.getIdToken()
    } catch {
      // Firebase not initialized — request will fail at the server
    }
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  }
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`

  const response = await fetch(
    `${chatApiUrl()}/api/chat/conversations/${conversationId}/messages`,
    {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: options.signal,
    },
  )

  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => "")
    yield {
      kind: "error",
      error: response.status === 429 ? "quota_exceeded" : "http_error",
      message: text || `Request failed with status ${response.status}`,
    }
    return
  }

  const reader = response.body
    .pipeThrough(new TextDecoderStream())
    .getReader()
  let buffer = ""
  let currentEventName = ""

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += value

      let nl: number
      while ((nl = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, nl)
        buffer = buffer.slice(nl + 2)
        const event = parseSseBlock(block)
        if (event) yield event
      }
      // Track partial event names if a block straddles chunks
      const lastNewline = buffer.lastIndexOf("\n")
      if (lastNewline !== -1) {
        const partial = buffer.slice(0, lastNewline)
        const match = /^event:\s*(.*)$/m.exec(partial)
        if (match) currentEventName = match[1]
      }
    }
  } finally {
    reader.releaseLock()
  }
  // Suppress unused-variable lint for the partial event name parse
  void currentEventName
}

function parseSseBlock(block: string): ChatStreamEvent | null {
  let eventName = "message"
  const dataLines: string[] = []
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim())
    }
  }
  if (dataLines.length === 0) return null
  let payload: Record<string, unknown>
  try {
    payload = JSON.parse(dataLines.join("\n"))
  } catch {
    return null
  }
  switch (eventName) {
    case "meta":
      return { kind: "meta", ...(payload as Record<string, never>) } as ChatStreamEvent
    case "delta":
      return { kind: "delta", text: String(payload.text ?? "") }
    case "done":
      return {
        kind: "done",
        output_tokens: Number(payload.output_tokens ?? 0),
        finish_reason: String(payload.finish_reason ?? "stop"),
      }
    case "error":
      return {
        kind: "error",
        error: String(payload.error ?? "error"),
        message: String(payload.message ?? ""),
      }
    default:
      return null
  }
}
