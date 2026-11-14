// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The passkey sign-in calls don't wait for a token.
 *
 * Asking the auth provider for a token waits for it to restore the stored
 * session, and that restore can wait indefinitely on another tab of the app
 * holding the browser's auth storage. A sign-in has no session to send, so
 * its calls must go out with that wait still pending. Here the provider's
 * getIdToken never settles, which is that tab's state exactly.
 *
 * Everything else keeps waiting for the token: an ordinary post, and the
 * step-up's finish, where the API uses the token to refuse a passkey that
 * belongs to another account.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const getIdToken = vi.fn()

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ getIdToken, signOut: vi.fn() }),
}))
vi.mock("../client.extensions", () => ({ apiErrorInterceptors: [] }))

const ASSERTION = {
  id: "cred",
  rawId: "cred",
  type: "public-key",
  response: { clientDataJSON: "", authenticatorData: "", signature: "" },
  clientExtensionResults: {},
} as const

let fetchMock: ReturnType<typeof vi.fn>

function sent(): { url: string; headers: Record<string, string> }[] {
  return fetchMock.mock.calls.map(([url, init]) => ({
    url: String(url),
    headers: (init as RequestInit).headers as Record<string, string>,
  }))
}

/** Let every pending microtask and zero-delay timer run. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

async function load() {
  vi.resetModules()
  const client = await import("../client")
  client.setApiUrl("http://test")
  return { client, passkey: await import("../passkey") }
}

beforeEach(() => {
  fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify({ custom_token: "ct", challenge: "c" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
  )
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  getIdToken.mockReset()
})

describe("with a token wait that never ends", () => {
  beforeEach(() => {
    getIdToken.mockReset().mockReturnValue(new Promise(() => {}))
  })

  it("beginAuthentication sends its request, with no Authorization header", async () => {
    const { passkey } = await load()
    await passkey.beginAuthentication()

    expect(sent()).toHaveLength(1)
    expect(sent()[0].url).toBe("http://test/api/auth/passkey/authenticate/begin")
    expect(sent()[0].headers).not.toHaveProperty("Authorization")
    expect(getIdToken).not.toHaveBeenCalled()
  })

  it("a sign-in's finishAuthentication sends its request, with no Authorization header", async () => {
    const { passkey } = await load()
    const result = await passkey.finishAuthentication(ASSERTION, "sign-in")

    expect(result.custom_token).toBe("ct")
    expect(sent()).toHaveLength(1)
    expect(sent()[0].url).toBe("http://test/api/auth/passkey/authenticate/finish")
    expect(sent()[0].headers).not.toHaveProperty("Authorization")
    expect(getIdToken).not.toHaveBeenCalled()
  })

  it("an ordinary post still waits for the token, and sends nothing meanwhile", async () => {
    const { client } = await load()
    void client.post("/api/anything", {})
    await flush()

    expect(getIdToken).toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("a step-up's finishAuthentication still waits for the token", async () => {
    const { passkey } = await load()
    void passkey.finishAuthentication(ASSERTION, "step-up")
    await flush()

    expect(getIdToken).toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe("with a token", () => {
  beforeEach(() => {
    getIdToken.mockReset().mockResolvedValue("tok")
  })

  it("an ordinary post sends it", async () => {
    const { client } = await load()
    await client.post("/api/anything", {})
    expect(sent()[0].headers.Authorization).toBe("Bearer tok")
  })

  it("a step-up's finishAuthentication sends it, so the API can bind the assertion", async () => {
    const { passkey } = await load()
    await passkey.finishAuthentication(ASSERTION, "step-up")
    expect(sent()[0].headers.Authorization).toBe("Bearer tok")
  })

  it("the sign-in calls still send none", async () => {
    const { passkey } = await load()
    await passkey.beginAuthentication()
    await passkey.finishAuthentication(ASSERTION, "sign-in")
    expect(sent().map((call) => call.headers.Authorization)).toEqual([undefined, undefined])
  })
})
