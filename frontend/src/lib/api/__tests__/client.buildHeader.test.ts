// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * X-Client-Build: when the bundle was built with NEXT_PUBLIC_CLIENT_BUILD, every
 * API request names that build, so a failed request can be matched to the code
 * the browser was actually running. A build without it sends no header at all —
 * never an empty one — because the API must not depend on it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

const getIdToken = vi.fn()

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ getIdToken, signOut: vi.fn() }),
}))
vi.mock("../client.extensions", () => ({ apiErrorInterceptors: [] }))

function ok200(): Response {
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "content-type": "application/json" },
  })
}

async function freshClient() {
  vi.resetModules()
  const mod = await import("../client")
  mod.setApiUrl("http://test")
  return mod
}

function sentHeaders(fetchMock: ReturnType<typeof vi.fn>, call: number): Record<string, string> {
  return (fetchMock.mock.calls[call][1] as RequestInit).headers as Record<string, string>
}

beforeEach(() => {
  getIdToken.mockReset().mockResolvedValue("tok")
})

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe("apiClient X-Client-Build", () => {
  it("sends the build on reads, writes and uploads", async () => {
    vi.stubEnv("NEXT_PUBLIC_CLIENT_BUILD", "f195b41")
    const client = await freshClient()
    const fetchMock = vi.fn().mockImplementation(async () => ok200())
    vi.stubGlobal("fetch", fetchMock)

    await client.apiClient("/api/x")
    await client.apiClient("/api/x", { method: "POST", body: JSON.stringify({ a: 1 }) })
    await client.apiClient("/api/x", { method: "POST", body: new FormData() })

    expect(fetchMock).toHaveBeenCalledTimes(3)
    for (const call of [0, 1, 2]) {
      expect(sentHeaders(fetchMock, call)["X-Client-Build"]).toBe("f195b41")
    }
  })

  it("keeps the header on the retry after a stale token", async () => {
    vi.stubEnv("NEXT_PUBLIC_CLIENT_BUILD", "f195b41")
    const client = await freshClient()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { code: "TOKEN_EXPIRED", message: "x" } }), {
          status: 401,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(ok200())
    vi.stubGlobal("fetch", fetchMock)

    await client.apiClient("/api/x")

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(sentHeaders(fetchMock, 1)["X-Client-Build"]).toBe("f195b41")
  })

  it.each([
    ["unset", undefined],
    ["empty", ""],
  ])("sends no header when the build variable is %s", async (_label, value) => {
    vi.stubEnv("NEXT_PUBLIC_CLIENT_BUILD", value)
    const client = await freshClient()
    const fetchMock = vi.fn().mockImplementation(async () => ok200())
    vi.stubGlobal("fetch", fetchMock)

    await client.apiClient("/api/x")

    expect(sentHeaders(fetchMock, 0)).not.toHaveProperty("X-Client-Build")
  })
})
