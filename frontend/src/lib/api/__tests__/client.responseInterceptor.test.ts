// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * registerResponseInterceptor: an observation seam so a deployment can react to
 * response shapes (maintenance banners, telemetry) without forking apiClient.
 * Covers: the observer fires on both success and error responses before the
 * body is parsed, it reads the body via a clone without stealing it from the
 * request path, a throwing observer never breaks the request, and the returned
 * disposer unregisters it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

const getIdToken = vi.fn()
const signOut = vi.fn()

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ getIdToken, signOut }),
}))
// No error-path interceptors in these tests — we exercise the response seam.
vi.mock("../client.extensions", () => ({ apiErrorInterceptors: [] }))

function ok200(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  })
}
function err500(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 500,
    headers: { "content-type": "application/json" },
  })
}

// Re-import the module fresh each test so the module-level interceptor array is
// reset between cases.
async function freshClient() {
  vi.resetModules()
  const mod = await import("../client")
  mod.setApiUrl("http://test")
  return mod
}

beforeEach(() => {
  getIdToken.mockReset().mockResolvedValue("tok")
  signOut.mockReset().mockResolvedValue(undefined)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("registerResponseInterceptor", () => {
  it("observes a successful response before parsing, without stealing its body", async () => {
    const client = await freshClient()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok200({ ok: true })))

    const seen: number[] = []
    let observedBody: unknown = null
    client.registerResponseInterceptor(async (response) => {
      seen.push(response.status)
      observedBody = await response.json() // reading the clone must not corrupt the real parse
    })

    // The request still resolves with the fully-parsed body...
    await expect(client.get("/api/x")).resolves.toEqual({ ok: true })
    // ...and the observer saw the response and could read its body.
    expect(seen).toEqual([200])
    expect(observedBody).toEqual({ ok: true })
  })

  it("fires on error responses too, before the ApiError is thrown", async () => {
    const client = await freshClient()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(err500({ error: { code: "BOOM" } })))

    const seen: number[] = []
    client.registerResponseInterceptor((response) => {
      seen.push(response.status)
    })

    await expect(client.get("/api/x")).rejects.toMatchObject({ status: 500 })
    expect(seen).toEqual([500])
  })

  it("swallows a throwing observer — the request path is unaffected", async () => {
    const client = await freshClient()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok200({ ok: true })))

    client.registerResponseInterceptor(() => {
      throw new Error("observer blew up")
    })

    await expect(client.get("/api/x")).resolves.toEqual({ ok: true })
  })

  it("runs every registered observer once per request", async () => {
    const client = await freshClient()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok200({ ok: true })))

    const a = vi.fn()
    const b = vi.fn()
    client.registerResponseInterceptor(a)
    client.registerResponseInterceptor(b)

    await client.get("/api/x")
    expect(a).toHaveBeenCalledTimes(1)
    expect(b).toHaveBeenCalledTimes(1)
  })

  it("the returned disposer unregisters the observer", async () => {
    const client = await freshClient()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok200({ ok: true })))

    const observer = vi.fn()
    const dispose = client.registerResponseInterceptor(observer)
    dispose()

    await client.get("/api/x")
    expect(observer).not.toHaveBeenCalled()
  })
})
