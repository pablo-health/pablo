// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Money conversion tests.
 *
 * The cases that matter are the ones that lose money silently: a decimal the
 * user typed exactly that binary floating point cannot represent, and inputs
 * that must refuse rather than resolve to zero.
 */

import { describe, it, expect } from "vitest"
import { dollarsToCents, formatCents } from "../money"

describe("formatCents", () => {
  it("renders stored cents as money", () => {
    expect(formatCents(15000, "usd")).toBe("$150.00")
    expect(formatCents(1, "usd")).toBe("$0.01")
    expect(formatCents(1234567, "usd")).toBe("$12,345.67")
  })
})

describe("dollarsToCents", () => {
  it("keeps the penny that float arithmetic loses", () => {
    // 160.10 * 100 is 16009.999… in binary floating point.
    expect(dollarsToCents("160.10")).toBe(16010)
    expect(dollarsToCents("0.29")).toBe(29)
  })

  it("accepts what a person actually types", () => {
    expect(dollarsToCents("150")).toBe(15000)
    expect(dollarsToCents("150.5")).toBe(15050)
    expect(dollarsToCents(" $1,250.00 ")).toBe(125000)
  })

  it("refuses anything that is not a positive amount", () => {
    for (const input of ["", "  ", ".", "0", "0.00", "abc", "1.234", "-5"]) {
      expect(dollarsToCents(input)).toBeNull()
    }
  })
})
