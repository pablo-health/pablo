// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The chat SSE consumer must route dead-session 401s through the shared
 * forced-logout flow — an inline chat error alone strands the user on a
 * session the backend has tombstoned (a token refresh can't revive it).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

const { handleTerminalAuthLogout } = vi.hoisted(() => ({
  handleTerminalAuthLogout: vi.fn(),
}))

vi.mock("@/lib/api/client", () => ({
  TOKEN_REFRESH_RETRY_CODES: new Set(["TOKEN_EXPIRED", "INVALID_TOKEN"]),
  TERMINAL_AUTH_CODES: new Set([
    "TOKEN_EXPIRED",
    "INVALID_TOKEN",
    "TOKEN_REVOKED",
    "USER_DISABLED",
  ]),
  buildApiUrl: (endpoint: string) => `http://test${endpoint}`,
  getAuthHeader: vi.fn().mockResolvedValue({ Authorization: "Bearer tok" }),
  handleTerminalAuthLogout,
}))

import { streamChatMessages } from "../sse"

function err401(code: string): Response {
  return new Response(JSON.stringify({ error: { code, message: code } }), {
    status: 401,
    headers: { "content-type": "application/json" },
  })
}

function callbacks() {
  return { onMeta: vi.fn(), onDelta: vi.fn(), onDone: vi.fn(), onError: vi.fn() }
}

beforeEach(() => {
  handleTerminalAuthLogout.mockReset()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("streamChatMessages terminal-auth handling", () => {
  it("boots with reason=idle_timeout on an idle 401 and still surfaces onError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(err401("IDLE_TIMEOUT")))
    const cb = callbacks()

    await streamChatMessages("conv-1", { content: "hi" }, cb)

    expect(handleTerminalAuthLogout).toHaveBeenCalledWith("idle_timeout")
    expect(cb.onError).toHaveBeenCalledWith(
      expect.objectContaining({ error: "auth_denied" }),
    )
  })

  it("boots with reason=session_expired when a retryable 401 survives the refresh retry", async () => {
    // A fresh Response per call — the consumer reads each body.
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(err401("TOKEN_EXPIRED")))
    vi.stubGlobal("fetch", fetchMock)
    const cb = callbacks()

    await streamChatMessages("conv-1", { content: "hi" }, cb)

    expect(fetchMock).toHaveBeenCalledTimes(2) // initial + force-refresh retry
    expect(handleTerminalAuthLogout).toHaveBeenCalledWith("session_expired")
    expect(cb.onError).toHaveBeenCalled()
  })

  it("boots with reason=session_expired on a revoked token without retrying", async () => {
    const fetchMock = vi.fn().mockResolvedValue(err401("TOKEN_REVOKED"))
    vi.stubGlobal("fetch", fetchMock)
    const cb = callbacks()

    await streamChatMessages("conv-1", { content: "hi" }, cb)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(handleTerminalAuthLogout).toHaveBeenCalledWith("session_expired")
  })

  it("does NOT boot on non-auth pre-stream errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("conflict", { status: 409 })),
    )
    const cb = callbacks()

    await streamChatMessages("conv-1", { content: "hi" }, cb)

    expect(handleTerminalAuthLogout).not.toHaveBeenCalled()
    expect(cb.onError).toHaveBeenCalledWith(
      expect.objectContaining({ error: "concurrent_turn" }),
    )
  })
})
