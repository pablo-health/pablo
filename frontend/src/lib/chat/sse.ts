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

import {
  TERMINAL_AUTH_CODES,
  TOKEN_REFRESH_RETRY_CODES,
  buildApiUrl,
  getAuthHeader,
  handleTerminalAuthLogout,
} from "@/lib/api/client"

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

  const doFetch = async (forceRefresh: boolean): Promise<Response> => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(await getAuthHeader(undefined, forceRefresh)),
    }
    return fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: callbacks.signal,
    })
  }

  let response = await doFetch(false)

  // A token-level 401 (stale/expired id token) is recoverable — the
  // Firebase SDK's proactive refresh lags in backgrounded tabs, so the
  // cached token can reach the backend dead. Force a refresh and retry
  // once. IDLE_TIMEOUT is intentionally not retried (refreshing would
  // defeat the idle control); it falls through to onError below.
  if (response.status === 401) {
    const firstBody = await safeReadText(response)
    if (isRetryableTokenError(firstBody)) {
      response = await doFetch(true)
    } else {
      // A dead session (idle timeout, revoked/disabled) must drive the
      // same clean sign-out + /login flow as apiClient — the chat panel's
      // inline error alone strands the user on a session the backend has
      // tombstoned (a token refresh can't revive it). onError still fires
      // so the panel renders its notice while the redirect happens.
      bootIfTerminalAuth(firstBody)
      callbacks.onError({
        error: mapHttpStatusToErrorCode(response.status),
        message: firstBody,
      })
      return
    }
  }

  if (!response.ok) {
    // Pre-stream HTTP errors (404 / 409 / 422), or a 401 that survived
    // the token-refresh retry above (terminal — boot). Surface as an
    // onError call so the panel renders the same error notice machinery
    // regardless of whether the failure happened before or during the
    // stream.
    const message = await safeReadText(response)
    if (response.status === 401) {
      bootIfTerminalAuth(message)
    }
    callbacks.onError({
      error: mapHttpStatusToErrorCode(response.status),
      message,
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

/**
 * True when a 401 response body carries a recoverable token-error code
 * (expired / invalid id token) — as opposed to IDLE_TIMEOUT or another
 * deliberate rejection. Parses the JSON ``{error:{code}}`` envelope;
 * unparseable bodies are treated as non-retryable.
 */
function isRetryableTokenError(body: string): boolean {
  try {
    const data = JSON.parse(body) as { error?: { code?: string } }
    return TOKEN_REFRESH_RETRY_CODES.has(data?.error?.code ?? "")
  } catch {
    return false
  }
}

/**
 * Route an unrecoverable 401 body through the shared forced-logout flow.
 * IDLE_TIMEOUT and the terminal token codes boot; anything else (e.g. an
 * unparseable body) falls through to the caller's generic error handling.
 */
function bootIfTerminalAuth(body: string): void {
  try {
    const code = (JSON.parse(body) as { error?: { code?: string } })?.error?.code ?? ""
    if (code === "IDLE_TIMEOUT") {
      handleTerminalAuthLogout("idle_timeout")
    } else if (TERMINAL_AUTH_CODES.has(code)) {
      handleTerminalAuthLogout("session_expired")
    }
  } catch {
    // Unparseable body — not a recognizable terminal auth error.
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
