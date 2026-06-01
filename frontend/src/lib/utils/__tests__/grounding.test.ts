// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect } from "vitest"
import { areAllGrounded, isTextGrounded } from "../grounding"

const SOURCE =
  "Client reported improved sleep and lower stress this week. " +
  "Affect was bright. Plan: continue weekly sessions and a breathing exercise."

describe("isTextGrounded", () => {
  it("is grounded for a verbatim substring", () => {
    expect(isTextGrounded("Affect was bright.", SOURCE)).toBe(true)
  })

  it("is grounded despite whitespace differences", () => {
    expect(isTextGrounded("Affect   was\nbright.", SOURCE)).toBe(true)
  })

  it("is grounded for high word overlap (recombined passages)", () => {
    // All words present in the source, but not one contiguous substring.
    expect(isTextGrounded("improved sleep and lower stress", SOURCE)).toBe(true)
  })

  it("is not grounded for fabricated text", () => {
    expect(isTextGrounded("Patient is training for a marathon.", SOURCE)).toBe(false)
  })

  it("treats empty text as grounded (not flagged)", () => {
    expect(isTextGrounded("", SOURCE)).toBe(true)
    expect(isTextGrounded("   ", SOURCE)).toBe(true)
  })
})

describe("areAllGrounded", () => {
  it("is true only when every item is grounded", () => {
    expect(
      areAllGrounded(["Affect was bright.", "continue weekly sessions"], SOURCE),
    ).toBe(true)
    expect(
      areAllGrounded(["Affect was bright.", "ran a marathon yesterday"], SOURCE),
    ).toBe(false)
  })
})
