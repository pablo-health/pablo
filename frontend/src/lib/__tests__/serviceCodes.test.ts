// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The suggestion list is a convenience, never an authority.
 *
 * It exists so a therapist can pick the code she already bills instead of
 * typing it from memory. It is not an allow-list, nothing is validated against
 * it, and no entry is ever selected on her behalf — which is why there is no
 * function here that takes a duration or a type name.
 */

import { describe, expect, it } from "vitest"
import {
  COMMON_SERVICE_CODES,
  describeServiceCode,
  normalizeServiceCode,
} from "@/lib/serviceCodes"

describe("the suggestion list", () => {
  it("offers the codes an outpatient therapy practice actually bills", () => {
    expect(COMMON_SERVICE_CODES.map((c) => c.code)).toContain("90837")
  })

  it("offers the codes a prescriber bills for a medical visit and its therapy add-on", () => {
    const codes = COMMON_SERVICE_CODES.map((c) => c.code)
    for (const code of [
      "99202", "99203", "99204", "99205",
      "99212", "99213", "99214", "99215",
      "90833", "90836", "90838",
    ]) {
      expect(codes).toContain(code)
    }
  })

  it("lists every code once", () => {
    const codes = COMMON_SERVICE_CODES.map((c) => c.code)
    expect(new Set(codes).size).toBe(codes.length)
  })

  it("describes each code in plain English rather than the AMA's descriptors", () => {
    // The official CPT descriptors are copyrighted text. These are here to
    // help someone recognise a code they already know.
    expect(COMMON_SERVICE_CODES.every((c) => c.description.length > 0)).toBe(true)
  })
})

describe("describeServiceCode", () => {
  it("names a code the list happens to know", () => {
    expect(describeServiceCode("90837")).toBe("Therapy session — 60 minutes or more")
  })

  it("names a medical visit and its therapy add-on", () => {
    expect(describeServiceCode("99214")).toBe("Medical visit, established — moderate complexity")
    expect(describeServiceCode("90836")).toBe("Therapy with a medical visit — around 45 minutes")
  })

  it("says nothing about a code it does not know rather than guessing", () => {
    expect(describeServiceCode("T1015")).toBeNull()
  })

  it("says nothing when no code is set", () => {
    expect(describeServiceCode(null)).toBeNull()
    expect(describeServiceCode("")).toBeNull()
  })

  it("recognises a code however it was typed", () => {
    expect(describeServiceCode("  90837 ")).toBe("Therapy session — 60 minutes or more")
  })
})

describe("normalizeServiceCode", () => {
  // Mirrors `normalize_service_code` on the API so the value she sees and the
  // value stored agree — a stray space would stop the code matching the
  // contracted rate filed under it.
  it("strips surrounding space", () => {
    expect(normalizeServiceCode(" 90837 ")).toBe("90837")
  })

  it("upper-cases so a HCPCS code matches its contracted rate", () => {
    expect(normalizeServiceCode("h0004")).toBe("H0004")
  })

  it("clears the code when the field is emptied rather than storing a blank", () => {
    expect(normalizeServiceCode("   ")).toBeNull()
  })

  it("keeps a code nobody has heard of", () => {
    expect(normalizeServiceCode("T1015")).toBe("T1015")
  })
})
