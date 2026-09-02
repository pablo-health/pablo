// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { assertHttpsOrigin, generateNonce } from "@/lib/auth/csp"

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

describe("generateNonce", () => {
  it("produces distinct base64 values on each call", () => {
    const a = generateNonce()
    const b = generateNonce()
    expect(a).not.toEqual(b)
    expect(a.length).toBeGreaterThan(0)
  })
})
