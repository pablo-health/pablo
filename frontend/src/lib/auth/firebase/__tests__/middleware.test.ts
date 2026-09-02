// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
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
    expect(response.headers.get("location")).toBeNull()
  })
})
