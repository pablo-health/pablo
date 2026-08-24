// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { DEFAULT_POST_LOGIN_DESTINATION, safeReturnTo } from "../returnTo"

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
