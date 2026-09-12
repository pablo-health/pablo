// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { readFileSync } from "fs"
import { join } from "path"
import { NextRequest, NextResponse } from "next/server"
import { beforeEach, describe, expect, it, vi } from "vitest"

import proxy from "../proxy"
import { authProviderMiddleware } from "@/lib/auth/middleware"

// Stubbing the provider chain keeps the edge auth stack (and its env
// requirements) out of the test environment; what matters here is only
// whether the proxy delegates to it.
vi.mock("@/lib/auth/middleware", () => ({
  authProviderMiddleware: vi.fn(() => NextResponse.next()),
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
