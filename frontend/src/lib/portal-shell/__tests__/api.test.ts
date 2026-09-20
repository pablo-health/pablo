// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The shell's fetch layer, at the level the backend contract lives: which
 * paths it calls, and that an unresolvable practice is one generic dead end
 * rather than several distinguishable ones.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { redeemInvite, resolvePortalPractice } from "../api"

vi.mock("@/lib/api/client", () => ({
  buildApiUrl: (endpoint: string) => `https://api.example.test${endpoint}`,
}))

function jsonResponse(body: unknown, init: { status?: number } = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "Content-Type": "application/json" },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("resolvePortalPractice", () => {
  it("reads the practice off the engine's resolve route", async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ slug: "example-therapy", display_name: "Example Therapy" }),
      )
    vi.stubGlobal("fetch", fetchSpy)

    const result = await resolvePortalPractice("example-therapy")

    expect(result).toEqual({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    expect(fetchSpy.mock.calls[0][0]).toContain("/api/portal/practices/example-therapy")
  })

  it("encodes a slug rather than pasting it into the path", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse({}, { status: 404 }))
    vi.stubGlobal("fetch", fetchSpy)

    await resolvePortalPractice("../patients")

    expect(fetchSpy.mock.calls[0][0]).toContain("/api/portal/practices/..%2Fpatients")
  })

  it("collapses a 404 and a network failure to the same answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, { status: 404 })))
    expect(await resolvePortalPractice("never-existed")).toEqual({ ok: false })

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")))
    expect(await resolvePortalPractice("never-existed")).toEqual({ ok: false })
  })
})

describe("redeemInvite", () => {
  it("posts both factors to the engine's redeem route", async () => {
    const minted = {
      session_token: "minted",
      token_type: "bearer",
      expires_at: 1,
      practice_slug: "example-therapy",
      practice_display_name: "Example Therapy",
    }
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(minted))
    vi.stubGlobal("fetch", fetchSpy)

    const result = await redeemInvite("invite-token", "123456")

    // The practice comes back with the credential: the page that redeems may
    // have arrived on a link that named no practice at all.
    expect(result).toEqual({ ok: true, data: minted })
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toContain("/api/patient/auth/redeem")
    expect(JSON.parse(init.body as string)).toEqual({ token: "invite-token", otp: "123456" })
  })
})
