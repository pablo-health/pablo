// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { needsEmailVerification } from "../emailVerification"

describe("needsEmailVerification", () => {
  it("is true when the provider says the email is unverified", () => {
    expect(needsEmailVerification({ email: "a@example.com", emailVerified: false })).toBe(true)
  })

  it("is false for a verified email", () => {
    expect(needsEmailVerification({ email: "a@example.com", emailVerified: true })).toBe(false)
  })

  // A provider that does not report the claim must not lock anyone out.
  it("is false when the provider does not report the claim", () => {
    expect(needsEmailVerification({ email: "a@example.com" })).toBe(false)
  })

  it("is false for a session with no email", () => {
    expect(needsEmailVerification({ emailVerified: false })).toBe(false)
  })
})
