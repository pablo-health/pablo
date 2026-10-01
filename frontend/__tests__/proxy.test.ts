// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

// @vitest-environment node
// The proxy runs in the Node.js runtime. The suite's default DOM environment
// supplies a browser `Request`, which drops the `Host` header as a forbidden
// name — and the portal-host rules read exactly that header.

import { readFileSync } from "fs"
import { join } from "path"
import { NextRequest, NextResponse } from "next/server"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import proxy from "../proxy"
import { authProviderMiddleware } from "@/lib/auth/middleware"
import { lookupPracticeHost } from "@/lib/portal-host/practice-host-lookup"

// Stubbing the provider chain keeps the edge auth stack (and its env
// requirements) out of the test environment; what matters here is only
// whether the proxy delegates to it.
vi.mock("@/lib/auth/middleware", () => ({
  authProviderMiddleware: vi.fn(() => NextResponse.next()),
}))

// The backend lookup is its own unit (practice-host-lookup.test.ts); here it
// only matters what the proxy does with each answer.
vi.mock("@/lib/portal-host/practice-host-lookup", () => ({
  lookupPracticeHost: vi.fn(async () => null),
}))

// proxy.ts (renamed from middleware.ts per the Next 16 convention)
// carries the matcher list that downstream deployment configs mirror,
// so its shape is a contract worth guarding directly. Asserting on the
// file text, not the imported config, avoids pulling the auth provider
// chain into the test environment.

const proxySource = readFileSync(join(__dirname, "..", "proxy.ts"), "utf8")

describe("frontend/proxy.ts matcher contract", () => {
  it("still exempts /api/login", () => {
    expect(proxySource).toContain("/api/login")
  })

  it("still exempts /api/logout", () => {
    expect(proxySource).toContain("/api/logout")
  })

  it("still bypasses the Firebase auth helper (__/) prefix", () => {
    expect(proxySource).toContain("__/")
  })
})

describe("frontend/proxy.ts static asset methods", () => {
  const chunkUrl = "https://example.test/_next/static/chunks/x.js"

  beforeEach(() => {
    vi.mocked(authProviderMiddleware).mockClear()
  })

  it("rejects a POST to a build asset path with 405 instead of letting it reach the action handler", async () => {
    const response = await proxy(new NextRequest(chunkUrl, { method: "POST" }))

    expect(response.status).toBe(405)
    expect(response.headers.get("Allow")).toBe("GET, HEAD")
    expect(await response.text()).toBe("")
    expect(authProviderMiddleware).not.toHaveBeenCalled()
  })

  it.each(["PUT", "DELETE", "PATCH"])("rejects %s to a build asset path too", async (method) => {
    const response = await proxy(new NextRequest(chunkUrl, { method }))

    expect(response.status).toBe(405)
  })

  it("passes a GET of a build asset straight through without a redirect", async () => {
    const response = await proxy(new NextRequest(chunkUrl, { method: "GET" }))

    expect(response.status).toBe(200)
    expect(response.headers.get("location")).toBeNull()
    expect(authProviderMiddleware).not.toHaveBeenCalled()
  })

  it("passes a HEAD of a build asset straight through", async () => {
    const response = await proxy(new NextRequest(chunkUrl, { method: "HEAD" }))

    expect(response.status).toBe(200)
    expect(authProviderMiddleware).not.toHaveBeenCalled()
  })

  it("still hands non-asset requests to the auth provider", async () => {
    await proxy(new NextRequest("https://example.test/patients", { method: "POST" }))

    expect(authProviderMiddleware).toHaveBeenCalledTimes(1)
  })

  it("matches build asset paths so the check can run at all", () => {
    expect(proxySource).toContain("/_next/static/:path*")
  })
})

describe("frontend/proxy.ts portal host", () => {
  const PORTAL = "portal.example.org"

  function at(url: string, host: string, method = "GET") {
    return new NextRequest(url, { method, headers: { host } })
  }

  beforeEach(() => {
    vi.mocked(authProviderMiddleware).mockClear()
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it("changes nothing when PORTAL_HOSTS is unset", async () => {
    vi.stubEnv("PORTAL_HOSTS", "")
    const response = await proxy(at("https://app.example.org/portal/acme?x=1", "app.example.org"))

    expect(response.status).toBe(200)
    expect(response.headers.get("location")).toBeNull()
    expect(response.headers.get("x-middleware-rewrite")).toBeNull()
    expect(authProviderMiddleware).toHaveBeenCalledTimes(1)
  })

  it("rewrites /{slug} on the portal host, carrying the provider's security headers", async () => {
    vi.stubEnv("PORTAL_HOSTS", PORTAL)
    vi.mocked(authProviderMiddleware).mockImplementationOnce(() => {
      const response = NextResponse.next({ request: { headers: new Headers({ "x-nonce": "abc" }) } })
      response.headers.set("Content-Security-Policy", "default-src 'self'")
      return response
    })

    const response = await proxy(at(`https://${PORTAL}/acme/recover?utm=1`, PORTAL))

    expect(response.headers.get("x-middleware-rewrite")).toBe(`https://${PORTAL}/portal/acme/recover?utm=1`)
    expect(response.headers.get("Content-Security-Policy")).toBe("default-src 'self'")
    expect(response.headers.get("x-middleware-request-x-nonce")).toBe("abc")
    expect(response.headers.get("location")).toBeNull()
    const seen = vi.mocked(authProviderMiddleware).mock.calls[0][0]
    expect(seen.nextUrl.pathname).toBe("/portal/acme/recover")
  })

  it("serves /portal/... on the portal host through the provider as it is", async () => {
    vi.stubEnv("PORTAL_HOSTS", PORTAL)
    const response = await proxy(at(`https://${PORTAL}/portal/acme`, PORTAL))

    expect(response.status).toBe(200)
    expect(response.headers.get("x-middleware-rewrite")).toBeNull()
    expect(authProviderMiddleware).toHaveBeenCalledTimes(1)
  })

  it.each(["/", "/dashboard", "/login", "/dashboard/patients", "/__/auth/handler", "/api/logout", "/api/auth/session"])(
    "answers %s on the portal host with a 404 and never a sign-in redirect",
    async (path) => {
      vi.stubEnv("PORTAL_HOSTS", PORTAL)
      const response = await proxy(at(`https://${PORTAL}${path}`, PORTAL))

      expect(response.status).toBe(404)
      expect(response.headers.get("location")).toBeNull()
      expect(authProviderMiddleware).not.toHaveBeenCalled()
    },
  )

  it("passes the frontend's own /api through on the portal host", async () => {
    vi.stubEnv("PORTAL_HOSTS", PORTAL)
    const response = await proxy(at(`https://${PORTAL}/api/config`, PORTAL))

    expect(response.status).toBe(200)
    expect(authProviderMiddleware).not.toHaveBeenCalled()
  })

  it("redirects /portal/... off the portal host with a 301, query kept", async () => {
    vi.stubEnv("PORTAL_HOSTS", `${PORTAL},second.example.org`)
    const response = await proxy(at("https://app.example.org/portal/acme/recover?a=1", "app.example.org"))

    expect(response.status).toBe(301)
    expect(response.headers.get("location")).toBe(`https://${PORTAL}/acme/recover?a=1`)
    expect(authProviderMiddleware).not.toHaveBeenCalled()
  })

  it("leaves the clinician host's own routes to the provider", async () => {
    vi.stubEnv("PORTAL_HOSTS", PORTAL)
    await proxy(at("https://app.example.org/dashboard", "app.example.org"))

    expect(authProviderMiddleware).toHaveBeenCalledTimes(1)
  })

  it("passes the Firebase helper through untouched off the portal host", async () => {
    for (const hosts of ["", PORTAL]) {
      vi.stubEnv("PORTAL_HOSTS", hosts)
      const response = await proxy(at("https://app.example.org/__/auth/handler", "app.example.org"))

      expect(response.status).toBe(200)
      expect(response.headers.get("location")).toBeNull()
    }
    expect(authProviderMiddleware).not.toHaveBeenCalled()
  })
})

describe("frontend/proxy.ts practice host", () => {
  const APP = "app.example.org"
  const PRIMARY = "portal.example.com"
  const ALIAS = "clients.example.com"

  function at(url: string, host: string) {
    return new NextRequest(url, { headers: { host } })
  }

  beforeEach(() => {
    vi.mocked(authProviderMiddleware).mockClear()
    vi.mocked(lookupPracticeHost).mockReset()
    vi.mocked(lookupPracticeHost).mockResolvedValue({ slug: "acme", primaryHost: PRIMARY })
    vi.stubEnv("APP_HOSTS", APP)
    vi.stubEnv("PORTAL_HOSTS", "portal.example.org")
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it("looks nothing up when APP_HOSTS is unset", async () => {
    vi.stubEnv("APP_HOSTS", "")
    await proxy(at(`https://${PRIMARY}/dashboard`, PRIMARY))

    expect(lookupPracticeHost).not.toHaveBeenCalled()
    expect(authProviderMiddleware).toHaveBeenCalledTimes(1)
  })

  it("looks nothing up for the app's own host, which is served as before", async () => {
    const response = await proxy(at(`https://${APP}/dashboard`, APP))

    expect(lookupPracticeHost).not.toHaveBeenCalled()
    expect(response.headers.get("x-middleware-rewrite")).toBeNull()
    expect(authProviderMiddleware).toHaveBeenCalledTimes(1)
  })

  it("serves the practice's portal at the root of its primary host, address unchanged", async () => {
    const response = await proxy(at(`https://${PRIMARY}/messaging?x=1`, `${PRIMARY}:443`))

    expect(lookupPracticeHost).toHaveBeenCalledWith(PRIMARY)
    expect(response.headers.get("x-middleware-rewrite")).toBe(`https://${PRIMARY}/portal/acme/messaging?x=1`)
    expect(response.headers.get("location")).toBeNull()
    const seen = vi.mocked(authProviderMiddleware).mock.calls[0][0]
    expect(seen.nextUrl.pathname).toBe("/portal/acme/messaging")
  })

  it("sends an alias to the primary with a 301", async () => {
    const response = await proxy(at(`https://${ALIAS}/forms?y=2`, ALIAS))

    expect(response.status).toBe(301)
    expect(response.headers.get("location")).toBe(`https://${PRIMARY}/forms?y=2`)
  })

  it("answers another practice's portal on this host with a 404", async () => {
    const response = await proxy(at(`https://${PRIMARY}/portal/other`, PRIMARY))

    expect(response.status).toBe(404)
    expect(authProviderMiddleware).not.toHaveBeenCalled()
  })

  it("moves the practice's own /portal/{slug} address to the root with a 301", async () => {
    const response = await proxy(at(`https://${PRIMARY}/portal/acme/recover`, PRIMARY))

    expect(response.status).toBe(301)
    expect(response.headers.get("location")).toBe(`https://${PRIMARY}/recover`)
  })

  it("answers a host that serves nothing with a 404, never the clinician app", async () => {
    vi.mocked(lookupPracticeHost).mockResolvedValue(null)
    for (const path of ["/", "/dashboard", "/login", "/portal/acme"]) {
      const response = await proxy(at(`https://unknown.example.com${path}`, "unknown.example.com"))
      expect(response.status, path).toBe(404)
      expect(response.headers.get("location"), path).toBeNull()
    }
    expect(authProviderMiddleware).not.toHaveBeenCalled()
  })

  it("answers 503 when the lookup could not be made", async () => {
    vi.mocked(lookupPracticeHost).mockResolvedValue("unavailable")
    const response = await proxy(at(`https://${PRIMARY}/`, PRIMARY))

    expect(response.status).toBe(503)
    expect(authProviderMiddleware).not.toHaveBeenCalled()
  })

  it("answers a host no practice could hold with a 404 without asking", async () => {
    const response = await proxy(at("https://bad_host.example.com/", "bad_host.example.com"))

    expect(response.status).toBe(404)
    expect(lookupPracticeHost).not.toHaveBeenCalled()
  })
})
