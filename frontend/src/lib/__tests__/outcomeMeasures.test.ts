// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tests for the UI-only instrument metadata helpers (PABLO-cwj).
 *
 * These guard the presentation contract — instrument lookup, severity badge
 * mapping, and the PHQ-9 item-9 safety-signal predicate. Scoring itself is the
 * backend's job and is not exercised here.
 */

import { describe, it, expect } from "vitest"
import {
  INSTRUMENTS,
  getInstrumentMeta,
  severityBadgeClasses,
  tripsSafetySignal,
} from "../outcomeMeasures"

describe("getInstrumentMeta", () => {
  it("returns PHQ-9 and GAD-7 with the expected item counts", () => {
    expect(getInstrumentMeta("phq9")?.items).toHaveLength(9)
    expect(getInstrumentMeta("gad7")?.items).toHaveLength(7)
  })

  it("returns undefined for an unknown code", () => {
    expect(getInstrumentMeta("nope")).toBeUndefined()
  })

  it("ships PHQ-9 first so it is the form default", () => {
    expect(INSTRUMENTS[0].code).toBe("phq9")
  })

  it("only configures a safety signal for PHQ-9 (item 9)", () => {
    expect(getInstrumentMeta("phq9")?.safetySignal?.itemKey).toBe("9")
    expect(getInstrumentMeta("gad7")?.safetySignal).toBeUndefined()
  })
})

describe("severityBadgeClasses", () => {
  it("maps known severities to distinct classes", () => {
    expect(severityBadgeClasses("minimal")).toContain("secondary")
    expect(severityBadgeClasses("severe")).toContain("red")
    expect(severityBadgeClasses("moderately severe")).toContain("orange")
  })

  it("falls back to neutral for null or unknown labels", () => {
    expect(severityBadgeClasses(null)).toContain("neutral")
    expect(severityBadgeClasses("brand-new-band")).toContain("neutral")
  })
})

describe("tripsSafetySignal", () => {
  const phq9 = getInstrumentMeta("phq9")
  const gad7 = getInstrumentMeta("gad7")

  it("trips when PHQ-9 item 9 is endorsed at or above threshold", () => {
    expect(tripsSafetySignal(phq9, { "9": 1 })).toBe(true)
    expect(tripsSafetySignal(phq9, { "9": 3 })).toBe(true)
  })

  it("does not trip when PHQ-9 item 9 is zero or absent", () => {
    expect(tripsSafetySignal(phq9, { "9": 0 })).toBe(false)
    expect(tripsSafetySignal(phq9, { "1": 3, "2": 2 })).toBe(false)
  })

  it("never trips for an instrument without a safety signal", () => {
    expect(tripsSafetySignal(gad7, { "7": 3 })).toBe(false)
  })

  it("is safe with null/undefined inputs", () => {
    expect(tripsSafetySignal(phq9, null)).toBe(false)
    expect(tripsSafetySignal(undefined, { "9": 3 })).toBe(false)
  })
})
