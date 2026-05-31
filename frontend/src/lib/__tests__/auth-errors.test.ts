// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { firebaseAuthErrorOutcome } from "../auth-errors"

// THERAPY-ivmo — sign-up surfacing generic auth/internal-error gives users
// nothing to act on. These tests pin the wrappers + extraction paths.

describe("firebaseAuthErrorOutcome — auth/internal-error", () => {
  it("extracts the inner HttpsError message from the wrapped string", () => {
    const err = {
      code: "auth/internal-error",
      message:
        'Firebase: An internal error has occurred ({"error":{"code":500,' +
        '"message":"Unable to verify authorization. Please try again later.",' +
        '"errors":[]}}). (auth/internal-error).',
    }
    expect(firebaseAuthErrorOutcome(err, "sign-up")).toEqual({
      kind: "message",
      message: "Unable to verify authorization. Please try again later.",
    })
  })

  it("falls back to a sign-up-flavored message when no inner JSON is present", () => {
    const err = {
      code: "auth/internal-error",
      message: "Firebase: An internal error has occurred. (auth/internal-error).",
    }
    const outcome = firebaseAuthErrorOutcome(err, "sign-up")
    expect(outcome.kind).toBe("message")
    if (outcome.kind === "message") {
      expect(outcome.message).toMatch(/sign-up/i)
      expect(outcome.message).toMatch(/double-check|administrator/i)
      expect(outcome.message).not.toContain("auth/internal-error")
    }
  })

  it("falls back to a sign-in-flavored message for the sign-in variant", () => {
    const err = {
      code: "auth/internal-error",
      message: "Firebase: An internal error has occurred. (auth/internal-error).",
    }
    const outcome = firebaseAuthErrorOutcome(err, "sign-in")
    expect(outcome.kind).toBe("message")
    if (outcome.kind === "message") {
      expect(outcome.message).toMatch(/authorization|try again/i)
      expect(outcome.message).not.toContain("auth/internal-error")
    }
  })
})

describe("firebaseAuthErrorOutcome — existing paths still work", () => {
  it("surfaces auth/blocking-function-error-response message as-is", () => {
    const err = {
      code: "auth/blocking-function-error-response",
      message: "Your email is not authorized to access this platform.",
    }
    expect(firebaseAuthErrorOutcome(err, "sign-up")).toEqual({
      kind: "message",
      message: "Your email is not authorized to access this platform.",
    })
  })

  it("returns mfa-required for auth/multi-factor-auth-required", () => {
    expect(
      firebaseAuthErrorOutcome({ code: "auth/multi-factor-auth-required" }, "sign-in"),
    ).toEqual({ kind: "mfa-required" })
  })

  it("returns popup-blocked for auth/popup-blocked", () => {
    expect(firebaseAuthErrorOutcome({ code: "auth/popup-blocked" }, "google")).toEqual({
      kind: "popup-blocked",
    })
  })

  it("returns noop for user-dismissed popup", () => {
    expect(
      firebaseAuthErrorOutcome({ code: "auth/popup-closed-by-user" }, "google"),
    ).toEqual({ kind: "noop" })
  })

  it("masks credential errors to a single message for security", () => {
    for (const code of [
      "auth/invalid-credential",
      "auth/user-not-found",
      "auth/wrong-password",
      "auth/user-disabled",
    ]) {
      expect(firebaseAuthErrorOutcome({ code }, "sign-in")).toEqual({
        kind: "message",
        message:
          "Invalid email or password. If you signed up with Google, use the “Continue with Google” button above.",
      })
    }
  })
})
