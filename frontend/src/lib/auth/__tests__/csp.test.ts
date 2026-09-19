// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import { assertHttpsOrigin, browserApiOrigin, generateNonce } from "@/lib/auth/csp"

afterEach(() => {
  vi.unstubAllEnvs()
})

describe("assertHttpsOrigin", () => {
  it("returns the normalized origin for a valid https URL", () => {
    expect(assertHttpsOrigin("API_URL", "https://api.example.com/v1")).toBe(
      "https://api.example.com"
    )
  })

  it("returns an empty string when the value is unset", () => {
    expect(assertHttpsOrigin("API_URL", "")).toBe("")
  })

  it("allows plain http for loopback hosts (local dev)", () => {
    expect(assertHttpsOrigin("API_URL", "http://localhost:8000")).toBe(
      "http://localhost:8000"
    )
  })

  it("throws for a non-https, non-loopback origin", () => {
    expect(() => assertHttpsOrigin("API_URL", "http://api.example.com")).toThrow(
      /must be an https origin/
    )
  })

  it("throws for a value that isn't a valid absolute URL", () => {
    expect(() => assertHttpsOrigin("API_URL", "not-a-url")).toThrow(
      /must be a valid absolute URL/
    )
  })
})

describe("browserApiOrigin", () => {
  it("is API_URL in the ordinary deployment, where both callers use one address", () => {
    vi.stubEnv("API_URL", "https://api.example.com")
    vi.stubEnv("PUBLIC_API_URL", "")
    expect(browserApiOrigin()).toBe("https://api.example.com")
  })

  it("prefers the published address when the server's is not reachable from a browser", () => {
    // The e2e stack: the frontend shares the backend's network namespace, so
    // server-rendered fetches use the container port while the browser can
    // only reach the published one. connect-src governs the browser.
    vi.stubEnv("API_URL", "http://localhost:8000")
    vi.stubEnv("PUBLIC_API_URL", "http://localhost:8210")
    expect(browserApiOrigin()).toBe("http://localhost:8210")
  })

  it("still refuses a plaintext non-loopback origin", () => {
    vi.stubEnv("PUBLIC_API_URL", "http://api.example.com")
    expect(() => browserApiOrigin()).toThrow(/must be an https origin/)
  })

  it("is empty when neither is set", () => {
    vi.stubEnv("API_URL", "")
    vi.stubEnv("PUBLIC_API_URL", "")
    expect(browserApiOrigin()).toBe("")
  })
})

describe("generateNonce", () => {
  it("produces distinct base64 values on each call", () => {
    const a = generateNonce()
    const b = generateNonce()
    expect(a).not.toEqual(b)
    expect(a.length).toBeGreaterThan(0)
  })
})
