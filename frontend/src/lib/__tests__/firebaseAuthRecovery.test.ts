// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { isFirebaseStuckStateError } from "../firebaseAuthRecovery"

// THERAPY-n1n6 — the detector is the load-bearing piece of the recovery
// flow. A false negative leaves users permanently stuck; a false positive
// kills auth state on an unrelated error and forces a reload. These tests
// pin both halves.

describe("isFirebaseStuckStateError", () => {
  it("matches the canonical stuck-state error string", () => {
    const err = new Error(
      'Auth (12.13.0): INTERNAL ASSERTION FAILED: Pending promise was never set',
    )
    expect(isFirebaseStuckStateError(err)).toBe(true)
  })

  it("matches when wrapped as an Unhandled Promise Rejection string", () => {
    const reason = new Error("INTERNAL ASSERTION FAILED: Pending promise was never set")
    // PromiseRejectionEvent-shaped object
    expect(isFirebaseStuckStateError({ reason })).toBe(true)
  })

  it("matches a bare string (logged but not Error-wrapped)", () => {
    expect(
      isFirebaseStuckStateError(
        "Auth: INTERNAL ASSERTION FAILED: Pending promise was never set",
      ),
    ).toBe(true)
  })

  it("does not match unrelated Firebase errors", () => {
    expect(
      isFirebaseStuckStateError(new Error("auth/popup-blocked")),
    ).toBe(false)
    expect(
      isFirebaseStuckStateError(new Error("Firebase: Quota exceeded.")),
    ).toBe(false)
  })

  it("does not match other INTERNAL ASSERTION FAILED text without the specific suffix", () => {
    expect(
      isFirebaseStuckStateError(
        new Error("INTERNAL ASSERTION FAILED: something completely different"),
      ),
    ).toBe(false)
  })

  it("does not throw or match on null/undefined/empty", () => {
    expect(isFirebaseStuckStateError(null)).toBe(false)
    expect(isFirebaseStuckStateError(undefined)).toBe(false)
    expect(isFirebaseStuckStateError("")).toBe(false)
    expect(isFirebaseStuckStateError({})).toBe(false)
  })
})
