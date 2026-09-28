// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { portalLocation, portalSectionHref } from "../paths"

describe("portalLocation", () => {
  it.each([
    ["/portal/acme", { base: "/portal/acme", section: null }],
    ["/portal/acme/", { base: "/portal/acme", section: null }],
    ["/portal/acme/refills", { base: "/portal/acme", section: "refills" }],
    ["/acme", { base: "/acme", section: null }],
    ["/acme/messaging", { base: "/acme", section: "messaging" }],
  ])("reads %s", (pathname, expected) => {
    expect(portalLocation("acme", pathname)).toEqual(expected)
  })

  it("falls back to Home on the /portal form for a path that is neither", () => {
    expect(portalLocation("acme", "/somewhere/else")).toEqual({
      base: "/portal/acme",
      section: null,
    })
  })

  it("does not mistake a slug called portal for the prefix", () => {
    expect(portalLocation("portal", "/portal/forms")).toEqual({
      base: "/portal",
      section: "forms",
    })
  })
})

describe("portalSectionHref", () => {
  it("adds the section to the base it is given", () => {
    expect(portalSectionHref("/acme", "refills")).toBe("/acme/refills")
    expect(portalSectionHref("/portal/acme", "refills")).toBe("/portal/acme/refills")
  })
})
