// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { beforeEach, describe, expect, it, vi } from "vitest"
import { NextRequest } from "next/server"

function scriptSrcDirective(csp: string): string {
  const directive = csp.split(";").find((d) => d.trim().startsWith("script-src"))
  if (!directive) throw new Error("script-src directive not found in CSP")
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

    expect(response.headers.get("Content-Security-Policy")).toContain("object-src 'none'")
  })
})
