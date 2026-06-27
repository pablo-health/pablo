// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * apiClient auth-failure handling: an unrecoverable 401 boots the user to
 * /login instead of stranding them on a logged-in-looking page that throws on
 * every action. Covers the recoverable refresh-retry, the terminal
 * session-expired redirect (for each terminal code), idle-timeout, MFA
 * pass-through, the caller-token scope, and the single-redirect re-entry guard.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

const getIdToken = vi.fn()
const signOut = vi.fn()

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ getIdToken, signOut }),
}))
// No error-path interceptors in these tests.
vi.mock("../client.extensions", () => ({ apiErrorInterceptors: [] }))

function err401(code: string): Response {
  return new Response(JSON.stringify({ error: { code, message: code } }), {
    status: 401,
    headers: { "content-type": "application/json" },
  })
}
function ok200(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  })
}

let assignSpy: ReturnType<typeof vi.fn>

// Re-import the module fresh each test so its module-level "logout in flight"
// guard is reset between cases.
async function freshClient() {
  vi.resetModules()
  const mod = await import("../client")
  mod.setApiUrl("http://test")
  return mod
}

beforeEach(() => {
  getIdToken.mockReset().mockResolvedValue("tok")
  signOut.mockReset().mockResolvedValue(undefined)
  assignSpy = vi.fn()
  // happy-dom's real location.assign would attempt a navigation; replace it.
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { assign: assignSpy, href: "http://test/" },
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("apiClient terminal-auth handling", () => {
  it("retries once on an expired token, then succeeds without redirecting", async () => {
    const client = await freshClient()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(err401("TOKEN_EXPIRED"))
      .mockResolvedValueOnce(ok200({ ok: true }))
    vi.stubGlobal("fetch", fetchMock)

    await expect(client.get("/api/x")).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(2) // initial + force-refresh retry
    expect(getIdToken).toHaveBeenNthCalledWith(2, true) // retry forces refresh
    expect(assignSpy).not.toHaveBeenCalled()
    expect(signOut).not.toHaveBeenCalled()
  })

  it("boots to /login?reason=session_expired when an expired token survives the retry", async () => {
    const client = await freshClient()
    const fetchMock = vi.fn().mockResolvedValue(err401("TOKEN_EXPIRED"))
    vi.stubGlobal("fetch", fetchMock)

    await expect(client.get("/api/x")).rejects.toMatchObject({ code: "TOKEN_EXPIRED" })
    expect(fetchMock).toHaveBeenCalledTimes(2) // initial + retry, both still 401
    await vi.waitFor(() => {
      expect(signOut).toHaveBeenCalledWith({ wipePersisted: true })
      expect(assignSpy).toHaveBeenCalledWith("/login?reason=session_expired")
    })
  })

  it.each(["TOKEN_REVOKED", "USER_DISABLED"])(
    "boots to /login for terminal code %s without retrying",
    async (code) => {
      const client = await freshClient()
      const fetchMock = vi.fn().mockResolvedValue(err401(code))
      vi.stubGlobal("fetch", fetchMock)

      await expect(client.get("/api/x")).rejects.toMatchObject({ code })
      expect(fetchMock).toHaveBeenCalledTimes(1) // not a refresh-retry code
      await vi.waitFor(() =>
        expect(assignSpy).toHaveBeenCalledWith("/login?reason=session_expired"),
      )
    },
  )

  it("redirects with reason=idle_timeout on an idle 401", async () => {
    const client = await freshClient()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(err401("IDLE_TIMEOUT")))

    await expect(client.get("/api/x")).rejects.toMatchObject({ code: "IDLE_TIMEOUT" })
    await vi.waitFor(() =>
      expect(assignSpy).toHaveBeenCalledWith("/login?reason=idle_timeout"),
    )
  })

  it("does NOT redirect on MFA_REQUIRED — that drives the step-up flow", async () => {
    const client = await freshClient()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(err401("MFA_REQUIRED")))

    await expect(client.get("/api/x")).rejects.toMatchObject({ code: "MFA_REQUIRED" })
    await Promise.resolve()
    expect(assignSpy).not.toHaveBeenCalled()
    expect(signOut).not.toHaveBeenCalled()
  })

  it("does NOT auto-redirect when the caller supplied its own token", async () => {
    const client = await freshClient()
    const fetchMock = vi.fn().mockResolvedValue(err401("TOKEN_EXPIRED"))
    vi.stubGlobal("fetch", fetchMock)

    await expect(client.get("/api/x", "caller-token")).rejects.toMatchObject({
      code: "TOKEN_EXPIRED",
    })
    expect(fetchMock).toHaveBeenCalledTimes(1) // retry + redirect are !token-scoped
    await Promise.resolve()
    expect(assignSpy).not.toHaveBeenCalled()
  })

  it("fires a single sign-out + redirect for a burst of terminal 401s", async () => {
    const client = await freshClient()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(err401("TOKEN_REVOKED")))

    await Promise.allSettled([
      client.get("/api/a"),
      client.get("/api/b"),
      client.get("/api/c"),
    ])
    await vi.waitFor(() => expect(assignSpy).toHaveBeenCalled())
    expect(assignSpy).toHaveBeenCalledTimes(1)
    expect(signOut).toHaveBeenCalledTimes(1)
  })
})
