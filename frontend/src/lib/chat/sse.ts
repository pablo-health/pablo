// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SSE consumer for ``POST /api/chat/conversations/{id}/messages``.
 *
 * The shared ``apiClient`` reads the response body up-front as JSON,
 * which collapses the stream. This helper instead uses
 * ``fetch + getReader()`` and a small line-based SSE parser. It shares
 * auth-header resolution with ``apiClient`` via ``getAuthHeader()``.
 *
 * Backend wire shape (design doc §8.1):
 *   event: meta\n
 *   data: {...}\n
 *   \n
 *   event: delta\n
 *   data: {...}\n
 *   \n
 *   ... (zero or more deltas) ...
 *   event: done | error\n
 *   data: {...}\n
 *   \n
 */

import { buildApiUrl, getAuthHeader } from "@/lib/api/client"

import type {
  ChatStreamCallbacks,
  ChatStreamDeltaEvent,
  ChatStreamDoneEvent,
  ChatStreamErrorEvent,
  ChatStreamMetaEvent,
  SendChatMessageRequest,
} from "./types"

export interface StreamChatMessagesOptions extends ChatStreamCallbacks {
  signal?: AbortSignal
}

interface ParsedEvent {
  event: string
  data: string
}

/**
 * Stream a single turn against the backend SSE endpoint.
 *
 * Resolves when the stream closes (after ``done`` or ``error`` lands).
 * Errors raised here are *transport*-level failures (bad fetch, parse
 * problem) — protocol-level errors arrive via ``onError`` per §8.2.
 */
export async function streamChatMessages(
  conversationId: string,
  body: SendChatMessageRequest,
  callbacks: StreamChatMessagesOptions,
): Promise<void> {
  const url = buildApiUrl(
    `/api/chat/conversations/${encodeURIComponent(conversationId)}/messages`,
  )
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    ...(await getAuthHeader()),
  }

  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: callbacks.signal,
  })

  if (!response.ok) {
    // Pre-stream HTTP errors (404 / 409 / 422). Surface as an onError
    // call so the panel renders the same error notice machinery
    // regardless of whether the failure happened before or during the
    // stream.
    const errorCode = mapHttpStatusToErrorCode(response.status)
    callbacks.onError({
      error: errorCode,
      message: await safeReadText(response),
    })
    return
  }

  if (!response.body) {
    callbacks.onError({
      error: "llm_error",
      message: "Empty response body from the chat endpoint.",
    })
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // SSE frames are separated by a blank line (\n\n). Walk every
      // complete frame in the buffer; keep the trailing partial.
      let separator = buffer.indexOf("\n\n")
      while (separator !== -1) {
        const rawFrame = buffer.slice(0, separator)
        buffer = buffer.slice(separator + 2)
        const parsed = parseFrame(rawFrame)
        if (parsed) {
          dispatch(parsed, callbacks)
        }
        separator = buffer.indexOf("\n\n")
      }
    }
    // Flush any final frame the server emitted without a trailing blank.
    if (buffer.trim()) {
      const parsed = parseFrame(buffer)
      if (parsed) dispatch(parsed, callbacks)
    }
  } finally {
    reader.releaseLock()
  }
}

function parseFrame(raw: string): ParsedEvent | null {
  let eventName = ""
  const dataLines: string[] = []
  for (const line of raw.split("\n")) {
    if (line.startsWith(":")) continue // comment / keepalive
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim())
    }
  }
  if (!eventName) return null
  return { event: eventName, data: dataLines.join("\n") }
}

function dispatch(frame: ParsedEvent, callbacks: ChatStreamCallbacks): void {
  let payload: unknown
  try {
    payload = frame.data ? JSON.parse(frame.data) : {}
  } catch {
    callbacks.onError({
      error: "llm_error",
      message: `Malformed SSE payload for event "${frame.event}".`,
    })
    return
  }

  switch (frame.event) {
    case "meta":
      callbacks.onMeta(payload as ChatStreamMetaEvent)
      return
    case "delta":
      callbacks.onDelta(payload as ChatStreamDeltaEvent)
      return
    case "done":
      callbacks.onDone(payload as ChatStreamDoneEvent)
      return
    case "error":
      callbacks.onError(payload as ChatStreamErrorEvent)
      return
    default:
      // Unknown event kinds are ignored — forwards-compat with future
      // protocol additions.
      return
  }
}

function mapHttpStatusToErrorCode(status: number): string {
  if (status === 401 || status === 403) return "auth_denied"
  if (status === 404) return "llm_error"
  if (status === 409) return "concurrent_turn"
  if (status === 422) return "message_too_long"
  return "llm_error"
}

async function safeReadText(response: Response): Promise<string> {
  try {
    const text = await response.text()
    return text || `Request failed with status ${response.status}.`
  } catch {
    return `Request failed with status ${response.status}.`
  }
}
