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

  it("allows Stripe.js to load and to open its card iframe, and nothing more", async () => {
    // Card collection needs exactly two directives: script-src to fetch the
    // library, frame-src for the iframe the card number is typed into. It is
    // deliberately absent from connect-src — Stripe.js talks to its API from
    // inside that iframe, under Stripe's own policy, so allowing api.stripe.com
    // here would widen egress on every page of the app and buy nothing. Both
    // halves are pinned because dropping either one is silent: the dialog still
    // opens, and the card field simply never appears.
    const { default: firebaseAuthMiddleware } = await import("../middleware")
    const request = new NextRequest("https://app.example.com/")

    const response = await firebaseAuthMiddleware(request)

    const csp = response.headers.get("Content-Security-Policy")!
    expect(namedDirective(csp, "script-src")).toContain("https://js.stripe.com")
    expect(namedDirective(csp, "frame-src")).toContain("https://js.stripe.com")
    expect(namedDirective(csp, "connect-src")).not.toContain("stripe.com")
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
