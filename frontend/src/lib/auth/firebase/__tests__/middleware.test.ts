// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { NextRequest, NextResponse } from "next/server"

vi.mock("@/lib/auth-config", () => ({
  authConfig: {},
  loginPath: "/api/login",
  logoutPath: "/api/logout",
}))

vi.mock("@/lib/auth/forced-logout", () => ({
  isForcedLogoutArrival: () => false,
}))

vi.mock("@/lib/auth/public-paths", () => ({
  extraPublicPaths: () => [],
}))

vi.mock("next-firebase-auth-edge", () => ({
  authMiddleware: async (
    _request: NextRequest,
    options: { handleInvalidToken: (reason: unknown) => Promise<NextResponse> }
  ) => options.handleInvalidToken("no-token"),
  redirectToLogin: (_request: NextRequest, { path }: { path: string }) =>
    NextResponse.redirect(new URL(path, "https://example.test")),
  redirectToHome: (_request: NextRequest, { path }: { path: string }) =>
    NextResponse.redirect(new URL(path, "https://example.test")),
}))

afterEach(() => {
  vi.unstubAllEnvs()
  vi.resetModules()
})

describe("firebaseAuthMiddleware", () => {
  it("redirects an unauthenticated request to /login even when DEV_MODE is set on a production build", async () => {
    vi.stubEnv("NODE_ENV", "production")
    vi.stubEnv("DEV_MODE", "true")
    vi.resetModules()

    const { default: firebaseAuthMiddleware } = await import("../middleware")
    const request = new NextRequest("https://example.test/dashboard")

    const response = await firebaseAuthMiddleware(request)

    expect(response.status).toBe(307)
    expect(response.headers.get("location")).toBe("https://example.test/login")
  })

  it("skips auth for a non-production build with DEV_MODE set", async () => {
    vi.stubEnv("NODE_ENV", "development")
    vi.stubEnv("DEV_MODE", "true")
    vi.resetModules()

    const { default: firebaseAuthMiddleware } = await import("../middleware")
    const request = new NextRequest("https://example.test/dashboard")

    const response = await firebaseAuthMiddleware(request)

    expect(response.status).toBe(200)
    expect(response.headers.get("location")).toBeNull()  })
})

function scriptSrcDirective(csp: string): string {
  return namedDirective(csp, "script-src")
}

function namedDirective(csp: string, name: string): string {
  const directive = csp.split(";").find((d) => d.trim().startsWith(`${name} `))
  if (!directive) throw new Error(`${name} directive not found in CSP`)
  return directive.trim()
}

describe("firebase middleware CSP", () => {
  beforeEach(() => {
    vi.resetModules()
    process.env.DEV_MODE = "true"
  })

  it("carries a nonce in script-src, with no 'unsafe-inline'", async () => {
    const { default: firebaseAuthMiddleware } = await import("../middleware")
    const request = new NextRequest("https://app.example.com/")

    const response = await firebaseAuthMiddleware(request)

    const csp = response.headers.get("Content-Security-Policy")
    expect(csp).toBeTruthy()
    const scriptSrc = scriptSrcDirective(csp!)
    expect(scriptSrc).not.toMatch(/unsafe-inline/)
    expect(scriptSrc).toMatch(/'nonce-[^']+'/)
  })

  it("allows the hosts Stripe documents for Elements", async () => {
    // Each of these is silent when missing: the dialog still opens and the
    // card field simply never appears, or — worse for the wildcard and the
    // 3-D Secure host — it works until the load where it doesn't, because
    // whether Stripe starts a frame on a subdomain or a bank demands a
    // challenge is not our decision. Pinned so nobody trims the list back to
    // whatever one local page load happened to need.
    const { default: firebaseAuthMiddleware } = await import("../middleware")
    const request = new NextRequest("https://app.example.com/")

    const response = await firebaseAuthMiddleware(request)

    const csp = response.headers.get("Content-Security-Policy")!
    const scriptSrc = namedDirective(csp, "script-src")
    const frameSrc = namedDirective(csp, "frame-src")

    expect(scriptSrc).toContain("https://js.stripe.com")
    expect(scriptSrc).toContain("https://*.js.stripe.com")

    expect(frameSrc).toContain("https://js.stripe.com")
    expect(frameSrc).toContain("https://*.js.stripe.com")
    expect(frameSrc).toContain("https://hooks.stripe.com")

    expect(namedDirective(csp, "connect-src")).toContain("https://api.stripe.com")
  })

  it("does not allow Stripe products this app does not use", async () => {
    // Checkout, Connect embedded components, Link and the crypto onramp all
    // have their own hosts in Stripe's CSP guide. None are used here, and a
    // policy is only as good as what it leaves out.
    const { default: firebaseAuthMiddleware } = await import("../middleware")
    const request = new NextRequest("https://app.example.com/")

    const csp = (await firebaseAuthMiddleware(request)).headers.get(
      "Content-Security-Policy"
    )!

    for (const host of [
      "checkout.stripe.com",
      "connect-js.stripe.com",
      "crypto-js.stripe.com",
      "link.com",
    ]) {
      expect(csp).not.toContain(host)
    }
  })

  it("threads the same nonce through to the request headers the theme script reads", async () => {
    const { default: firebaseAuthMiddleware } = await import("../middleware")
    const request = new NextRequest("https://app.example.com/")

    const response = await firebaseAuthMiddleware(request)

    const csp = response.headers.get("Content-Security-Policy")!
    const [, nonce] = scriptSrcDirective(csp).match(/'nonce-([^']+)'/) ?? []
    expect(nonce).toBeTruthy()

    // Next.js propagates request headers via x-middleware-request-<name>;
    // this is what `headers().get("x-nonce")` resolves to in the layout.
    expect(response.headers.get("x-middleware-request-x-nonce")).toBe(nonce)
  })

  it("adds form-action 'self' https://accounts.google.com", async () => {
    const { default: firebaseAuthMiddleware } = await import("../middleware")
    const request = new NextRequest("https://app.example.com/")

    const response = await firebaseAuthMiddleware(request)

    expect(response.headers.get("Content-Security-Policy")).toContain(
      "form-action 'self' https://accounts.google.com"
    )
  })

  it("keeps object-src 'none'", async () => {
    const { default: firebaseAuthMiddleware } = await import("../middleware")
    const request = new NextRequest("https://app.example.com/")

    const response = await firebaseAuthMiddleware(request)

    expect(response.headers.get("Content-Security-Policy")).toContain("object-src 'none'")  })
})
