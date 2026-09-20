// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Portal-shell session persistence.
 *
 * Pins: a successful redeem persists the token under a per-slug
 * `localStorage` key; the bootstrap probe rotates the stored token via
 * `/refresh` and re-persists the ROTATED one; a 401 off refresh clears
 * storage; and — the load-bearing isolation property — none of this ever
 * reaches the clinician API client (`get`/`post` from `@/lib/api/client`),
 * which would attach a Firebase bearer instead of the patient's own.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import {
  bootstrapSession,
  clearSession,
  getStoredSession,
  redeemAndStore,
  storeSession,
} from "../session"

const clinicianGet = vi.fn()
const clinicianPost = vi.fn()

vi.mock("@/lib/api/client", () => ({
  buildApiUrl: (endpoint: string) => `https://api.example.test${endpoint}`,
  get: (...args: unknown[]) => clinicianGet(...args),
  post: (...args: unknown[]) => clinicianPost(...args),
}))

const SLUG = "example-therapy"

function jsonResponse(body: unknown, init: { status?: number } = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "Content-Type": "application/json" },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
})

describe("session storage", () => {
  it("persists and reads a session under a per-slug key", () => {
    storeSession(SLUG, { sessionToken: "tok-abc", expiresAt: 1000 })

    expect(getStoredSession(SLUG)).toEqual({ sessionToken: "tok-abc", expiresAt: 1000 })
    expect(getStoredSession("some-other-practice")).toBeNull()
  })

  it("writes under the portal-namespaced key", () => {
    storeSession(SLUG, { sessionToken: "tok-abc", expiresAt: 1000 })

    const raw = window.localStorage.getItem(`pablo-portal-session:${SLUG}`)
    expect(raw).not.toBeNull()
    expect(JSON.parse(raw as string)).toEqual({ sessionToken: "tok-abc", expiresAt: 1000 })
  })

  it("clears only the named slug's session", () => {
    storeSession(SLUG, { sessionToken: "tok-abc", expiresAt: 1000 })
    storeSession("other-practice", { sessionToken: "tok-xyz", expiresAt: 2000 })

    clearSession(SLUG)

    expect(getStoredSession(SLUG)).toBeNull()
    expect(getStoredSession("other-practice")).not.toBeNull()
  })
})

describe("redeemAndStore", () => {
  function minted() {
    return jsonResponse({
      session_token: "minted-token",
      token_type: "bearer",
      expires_at: 999,
    })
  }

  it("returns the minted token", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(minted()))

    const result = await redeemAndStore(SLUG, "invite-token", "123456")

    expect(result).toEqual({ ok: true, sessionToken: "minted-token" })
  })

  /**
   * Under the practice whose page the link opened, which is the key the
   * shell will look under on the next visit.
   */
  it("persists the minted token under the practice's slug", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(minted()))

    await redeemAndStore(SLUG, "invite-token", "123456")

    expect(getStoredSession(SLUG)).toEqual({ sessionToken: "minted-token", expiresAt: 999 })
  })

  it("stores nothing on a failed redeem", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, { status: 401 })))

    const result = await redeemAndStore(SLUG, "invite-token", "000000")

    expect(result).toEqual({ ok: false })
    expect(getStoredSession(SLUG)).toBeNull()
  })
})

describe("bootstrapSession", () => {
  it("reports 'none' when there is no stored session", async () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal("fetch", fetchSpy)

    const result = await bootstrapSession(SLUG)

    expect(result).toEqual({ status: "none" })
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it("calls /refresh and stores the ROTATED token when the stored one is live", async () => {
    storeSession(SLUG, { sessionToken: "old-token", expiresAt: 100 })
    const fetchSpy = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ session_token: "rotated-token", token_type: "bearer", expires_at: 200 }),
      )
    vi.stubGlobal("fetch", fetchSpy)

    const result = await bootstrapSession(SLUG)

    expect(result).toEqual({ status: "active", sessionToken: "rotated-token" })
    expect(getStoredSession(SLUG)).toEqual({ sessionToken: "rotated-token", expiresAt: 200 })
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toContain("/api/patient/auth/refresh")
    expect(JSON.parse(init.body as string)).toEqual({ session_token: "old-token" })
  })

  it("clears storage and reports 'expired' on a 401 from refresh", async () => {
    storeSession(SLUG, { sessionToken: "dead-token", expiresAt: 100 })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, { status: 401 })))

    const result = await bootstrapSession(SLUG)

    expect(result).toEqual({ status: "expired" })
    expect(getStoredSession(SLUG)).toBeNull()
  })

  it("never reaches the clinician API client", async () => {
    storeSession(SLUG, { sessionToken: "old-token", expiresAt: 100 })
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          session_token: "rotated-token",
          token_type: "bearer",
          expires_at: 200,
        }),
      ),
    )

    await bootstrapSession(SLUG)
    await redeemAndStore(SLUG, "invite-token", "123456")

    expect(clinicianGet).not.toHaveBeenCalled()
    expect(clinicianPost).not.toHaveBeenCalled()
  })
})
