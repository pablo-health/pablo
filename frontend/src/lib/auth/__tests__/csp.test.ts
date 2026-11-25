// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import {
  assertHttpsOrigin,
  browserApiOrigin,
  browserStorageOrigin,
  extraSources,
  generateNonce,
} from "@/lib/auth/csp"

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

describe("browserStorageOrigin", () => {
  it("is empty on a deployment that does not name a store", () => {
    // Google-managed: signed URLs are on storage.googleapis.com, which the
    // Firebase policy already allows through its wildcard.
    vi.stubEnv("PUBLIC_FILE_STORAGE_URL", "")
    expect(browserStorageOrigin()).toBe("")
  })

  it("names an S3-compatible store, which no wildcard covers", () => {
    vi.stubEnv("PUBLIC_FILE_STORAGE_URL", "https://s3.us-east-1.amazonaws.com")
    expect(browserStorageOrigin()).toBe("https://s3.us-east-1.amazonaws.com")
  })

  it("allows a loopback store, for a local stack", () => {
    vi.stubEnv("PUBLIC_FILE_STORAGE_URL", "http://localhost:9000")
    expect(browserStorageOrigin()).toBe("http://localhost:9000")
  })

  it("still refuses a plaintext non-loopback store", () => {
    vi.stubEnv("PUBLIC_FILE_STORAGE_URL", "http://storage.example.com")
    expect(() => browserStorageOrigin()).toThrow(/must be an https origin/)
  })
})

describe("extraSources", () => {
  it("is empty when the variable is unset", () => {
    vi.stubEnv("CSP_EXTRA_SCRIPT_SRC", "")
    expect(extraSources("CSP_EXTRA_SCRIPT_SRC")).toBe("")
  })

  it("passes origins and origin-plus-path URLs through, whitespace normalised", () => {
    vi.stubEnv(
      "CSP_EXTRA_SCRIPT_SRC",
      "  https://cdn.example.com/lib/v2/init.js\n\thttps://api.example.com   http://localhost:9000 "
    )
    expect(extraSources("CSP_EXTRA_SCRIPT_SRC")).toBe(
      "https://cdn.example.com/lib/v2/init.js https://api.example.com http://localhost:9000"
    )
  })

  it.each([
    "'unsafe-inline'",
    "'unsafe-eval'",
    "'self'",
    "'nonce-abc'",
    "*",
    "https:",
    "data:",
    "https://*.example.com",
    "http://example.com",
    "https://example.com;",
    "https://a.example.com,https://b.example.com",
    "https://example.com/?q=1",
    "https://user@example.com",
    "example.com",
  ])("refuses %s, naming the variable", (value) => {
    vi.stubEnv("CSP_EXTRA_FRAME_SRC", `https://ok.example.com ${value}`)
    expect(() => extraSources("CSP_EXTRA_FRAME_SRC")).toThrow(/CSP_EXTRA_FRAME_SRC/)
  })

  it("refuses a value that smuggles in another directive", () => {
    vi.stubEnv("CSP_EXTRA_CONNECT_SRC", "https://ok.example.com; script-src *")
    expect(() => extraSources("CSP_EXTRA_CONNECT_SRC")).toThrow(/CSP_EXTRA_CONNECT_SRC/)
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
