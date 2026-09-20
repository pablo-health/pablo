// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The three calls a signed-in patient makes, plus recovery.
 *
 * These are the first calls on this surface that authenticate — resolve,
 * redeem and refresh all run before or across a session — so what is under
 * test is mostly the bearer header, the paths, and one asymmetry:
 * `requestPortalRecovery` reports whether the REQUEST got through and never
 * whether an account was found, because the server does not tell it.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fetchCapabilities, requestPortalRecovery, signOut } from "../api"
import { signOutAndForget } from "../session"

vi.mock("@/lib/api/client", () => ({
  buildApiUrl: (endpoint: string) => `https://api.example.test${endpoint}`,
}))

const TOKEN = "live-session-token"
const SLUG = "example-therapy"

function jsonResponse(body: unknown, init: { status?: number } = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "Content-Type": "application/json" },
  })
}

function stubFetch(response: Response | Error) {
  const spy =
    response instanceof Error
      ? vi.fn().mockRejectedValue(response)
      : vi.fn().mockResolvedValue(response)
  vi.stubGlobal("fetch", spy)
  return spy
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
})

describe("signOut", () => {
  it("posts to the one-device route with the session as the bearer", async () => {
    const fetchSpy = stubFetch(jsonResponse({ sessions_revoked: 1 }))

    const result = await signOut(TOKEN)

    expect(result).toEqual({ ok: true, data: { sessions_revoked: 1 } })
    expect(fetchSpy.mock.calls[0][0]).toContain("/api/patient/auth/logout")
    expect(fetchSpy.mock.calls[0][1].method).toBe("POST")
    expect(fetchSpy.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${TOKEN}`)
  })

  it("posts to the every-device route when asked", async () => {
    const fetchSpy = stubFetch(jsonResponse({ sessions_revoked: 3 }))

    await signOut(TOKEN, { everywhere: true })

    expect(fetchSpy.mock.calls[0][0]).toContain("/api/patient/auth/logout-all")
  })

  it("reports a refusal rather than throwing", async () => {
    /** A 403 on the every-device route means step-up is required. */
    stubFetch(jsonResponse({ error: {} }, { status: 403 }))

    expect(await signOut(TOKEN, { everywhere: true })).toEqual({ ok: false })
  })

  it("reports a network failure rather than throwing", async () => {
    stubFetch(new TypeError("offline"))

    expect(await signOut(TOKEN)).toEqual({ ok: false })
  })
})

describe("signOutAndForget", () => {
  it("clears the stored session after a successful sign-out", async () => {
    window.localStorage.setItem(
      `pablo-portal-session:${SLUG}`,
      JSON.stringify({ sessionToken: TOKEN, expiresAt: 9_999_999_999 }),
    )
    stubFetch(jsonResponse({ sessions_revoked: 1 }))

    const result = await signOutAndForget(SLUG, TOKEN)

    expect(result).toEqual({ revoked: true })
    expect(window.localStorage.getItem(`pablo-portal-session:${SLUG}`)).toBeNull()
  })

  it("clears the stored session even when the server call fails", async () => {
    /**
     * Somebody signing out on a shared computer is leaving. The useful
     * thing to do with a token whose revocation could not be confirmed is
     * to stop holding it.
     */
    window.localStorage.setItem(
      `pablo-portal-session:${SLUG}`,
      JSON.stringify({ sessionToken: TOKEN, expiresAt: 9_999_999_999 }),
    )
    stubFetch(new TypeError("offline"))

    const result = await signOutAndForget(SLUG, TOKEN)

    expect(result).toEqual({ revoked: false })
    expect(window.localStorage.getItem(`pablo-portal-session:${SLUG}`)).toBeNull()
  })

  it("leaves another practice's stored session alone", async () => {
    window.localStorage.setItem(
      "pablo-portal-session:other-practice",
      JSON.stringify({ sessionToken: "theirs", expiresAt: 9_999_999_999 }),
    )
    stubFetch(jsonResponse({ sessions_revoked: 1 }))

    await signOutAndForget(SLUG, TOKEN)

    expect(window.localStorage.getItem("pablo-portal-session:other-practice")).not.toBeNull()
  })
})

describe("fetchCapabilities", () => {
  it("reads the document with the session as the bearer", async () => {
    const document = {
      practice: { display_name: "Example Therapy" },
      modules: { intake: true, messaging: false },
      auth_strength: "stepped_up",
    }
    const fetchSpy = stubFetch(jsonResponse(document))

    const result = await fetchCapabilities(TOKEN)

    expect(result).toEqual({ ok: true, data: document })
    expect(fetchSpy.mock.calls[0][0]).toContain("/api/patient/capabilities")
    expect(fetchSpy.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${TOKEN}`)
  })

  it("reports a failure rather than inventing a document", async () => {
    /**
     * The caller reads `{ ok: false }` as "draw everything", which is only
     * safe because it is unambiguous — an empty module map would read as
     * "this practice has nothing" and hide a working portal.
     */
    stubFetch(jsonResponse({}, { status: 401 }))

    expect(await fetchCapabilities(TOKEN)).toEqual({ ok: false })
  })
})

describe("requestPortalRecovery", () => {
  it("posts the address to the practice's recovery route", async () => {
    const fetchSpy = stubFetch(new Response(null, { status: 202 }))

    const result = await requestPortalRecovery(SLUG, "ada@example.test")

    expect(result).toEqual({ ok: true })
    expect(fetchSpy.mock.calls[0][0]).toContain(`/api/portal/practices/${SLUG}/recover`)
    expect(JSON.parse(fetchSpy.mock.calls[0][1].body)).toEqual({ email: "ada@example.test" })
  })

  it("encodes the slug rather than pasting it into the path", async () => {
    const fetchSpy = stubFetch(new Response(null, { status: 202 }))

    await requestPortalRecovery("a/b?c", "ada@example.test")

    expect(fetchSpy.mock.calls[0][0]).toContain("a%2Fb%3Fc")
  })

  it("carries no CAPTCHA header when there is no token", async () => {
    const fetchSpy = stubFetch(new Response(null, { status: 202 }))

    await requestPortalRecovery(SLUG, "ada@example.test", null)

    expect(fetchSpy.mock.calls[0][1].headers["X-Captcha-Token"]).toBeUndefined()
  })

  it("carries the CAPTCHA token on the header the engine reads", async () => {
    const fetchSpy = stubFetch(new Response(null, { status: 202 }))

    await requestPortalRecovery(SLUG, "ada@example.test", "captcha-token")

    expect(fetchSpy.mock.calls[0][1].headers["X-Captcha-Token"]).toBe("captcha-token")
  })

  it("reports ok for an address nobody has, because the server does", async () => {
    /**
     * The 202 is uniform on purpose. `ok: true` here means "the request
     * went through", and the page must not read it as "an account exists".
     */
    stubFetch(new Response(null, { status: 202 }))

    expect(await requestPortalRecovery(SLUG, "stranger@example.test")).toEqual({ ok: true })
  })

  it("reports not-ok for a closed rate-limit window", async () => {
    stubFetch(jsonResponse({ detail: "Too many requests" }, { status: 429 }))

    expect(await requestPortalRecovery(SLUG, "ada@example.test")).toEqual({ ok: false })
  })

  it("reports not-ok for a network failure", async () => {
    stubFetch(new TypeError("offline"))

    expect(await requestPortalRecovery(SLUG, "ada@example.test")).toEqual({ ok: false })
  })
})
