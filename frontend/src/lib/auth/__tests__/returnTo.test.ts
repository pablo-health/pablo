// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { DEFAULT_POST_LOGIN_DESTINATION, safeContinuePath, safeReturnTo } from "../returnTo"

describe("safeReturnTo", () => {
  it("returns the interrupted page, query and hash intact", () => {
    expect(safeReturnTo("/dashboard/calendar?view=week#slot-3")).toBe(
      "/dashboard/calendar?view=week#slot-3",
    )
  })

  it("falls back when there is no returnTo at all", () => {
    for (const empty of [null, undefined, ""]) {
      expect(safeReturnTo(empty)).toBe(DEFAULT_POST_LOGIN_DESTINATION)
    }
  })

  it("rejects an absolute URL to another origin", () => {
    expect(safeReturnTo("https://evil.example/steal")).toBe(
      DEFAULT_POST_LOGIN_DESTINATION,
    )
  })

  it("rejects the protocol-relative form", () => {
    // The subtle half of an open redirect: "//evil.example" satisfies a naive
    // startsWith("/") check, and the browser still resolves it off-origin.
    expect(safeReturnTo("//evil.example/steal")).toBe(
      DEFAULT_POST_LOGIN_DESTINATION,
    )
  })

  it("rejects a scheme that is not a path at all", () => {
    expect(safeReturnTo("javascript:alert(1)")).toBe(
      DEFAULT_POST_LOGIN_DESTINATION,
    )
  })

  it("refuses to send the user back to the login screen", () => {
    // Otherwise a boot that fires while already on /login round-trips into
    // itself and the user can never leave.
    expect(safeReturnTo("/login")).toBe(DEFAULT_POST_LOGIN_DESTINATION)
    expect(safeReturnTo("/login?reason=idle_timeout")).toBe(
      DEFAULT_POST_LOGIN_DESTINATION,
    )
  })

  it("does not mistake a legitimate path that merely starts with the same letters", () => {
    // "/logins-report" is a real page as far as this helper knows; only the
    // /login route itself and its children are excluded.
    expect(safeReturnTo("/logins-report")).toBe("/logins-report")
  })
})

describe("safeContinuePath", () => {
  const origin = "https://app.example"

  it("keeps a same-origin absolute URL as a path, query and hash intact", () => {
    expect(safeContinuePath("https://app.example/mfa-enrollment?step=2#totp", origin)).toBe(
      "/mfa-enrollment?step=2#totp",
    )
  })

  it("keeps a bare path", () => {
    expect(safeContinuePath("/onboarding", origin)).toBe("/onboarding")
  })

  it("falls back to the sign-in page when there is nothing to continue to", () => {
    for (const empty of [null, undefined, ""]) {
      expect(safeContinuePath(empty, origin)).toBe("/login")
    }
  })

  it("rejects an absolute URL on another origin", () => {
    // The whole point: a crafted reset link must not end on a look-alike login.
    expect(safeContinuePath("https://evil.example/login", origin)).toBe("/login")
    expect(safeContinuePath("https://app.example.evil.example/login", origin)).toBe("/login")
  })

  it("rejects the protocol-relative form", () => {
    expect(safeContinuePath("//evil.example/login", origin)).toBe("/login")
  })

  it("rejects non-http schemes", () => {
    expect(safeContinuePath("javascript:alert(1)", origin)).toBe("/login")
    expect(safeContinuePath("data:text/html,hi", origin)).toBe("/login")
  })

  it("honours a caller-supplied fallback", () => {
    expect(safeContinuePath("https://evil.example/x", origin, "/dashboard")).toBe("/dashboard")
  })
})
