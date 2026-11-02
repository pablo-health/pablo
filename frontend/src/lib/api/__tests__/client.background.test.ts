// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * apiClient sends `X-Pablo-Background: 1` for requests started inside
 * runAsBackground and for no others, and keeps it on the one retry it makes
 * after a stale-token 401 (which happens after the mark has ended).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { runAsBackground } from "../backgroundRequests"
import { get, setApiUrl } from "../client"

const getIdToken = vi.fn()

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ getIdToken, signOut: vi.fn() }),
}))
vi.mock("../client.extensions", () => ({ apiErrorInterceptors: [] }))

const fetchMock = vi.fn()

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  })
}

function backgroundHeader(call: number): string | undefined {
  return (fetchMock.mock.calls[call][1].headers as Record<string, string>)["X-Pablo-Background"]
}

beforeEach(() => {
  setApiUrl("http://test")
  getIdToken.mockReset().mockResolvedValue("tok")
  fetchMock.mockReset().mockImplementation(() => Promise.resolve(json(200, {})))
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("apiClient background requests", () => {
  it("does not mark an ordinary request", async () => {
    await get("/api/x")

    expect(backgroundHeader(0)).toBeUndefined()
  })

  it("marks a request started inside runAsBackground", async () => {
    await runAsBackground(() => get("/api/x"))

    expect(backgroundHeader(0)).toBe("1")
  })

  it("does not mark a request started while a background one is in flight", async () => {
    const background = runAsBackground(() => get("/api/poll"))
    const foreground = get("/api/click")
    await Promise.all([background, foreground])

    const byUrl = Object.fromEntries(
      fetchMock.mock.calls.map(([url], i) => [url as string, backgroundHeader(i)]),
    )
    expect(byUrl["http://test/api/poll"]).toBe("1")
    expect(byUrl["http://test/api/click"]).toBeUndefined()
  })

  it("keeps the mark on the retry after a stale token", async () => {
    fetchMock
      .mockResolvedValueOnce(json(401, { detail: { error: { code: "TOKEN_EXPIRED" } } }))
      .mockResolvedValueOnce(json(200, {}))

    await runAsBackground(() => get("/api/x"))

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(backgroundHeader(0)).toBe("1")
    expect(backgroundHeader(1)).toBe("1")
  })

  it("does not add the mark to the retry of an ordinary request", async () => {
    fetchMock
      .mockResolvedValueOnce(json(401, { detail: { error: { code: "TOKEN_EXPIRED" } } }))
      .mockResolvedValueOnce(json(200, {}))

    await get("/api/x")

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(backgroundHeader(1)).toBeUndefined()
  })
})
