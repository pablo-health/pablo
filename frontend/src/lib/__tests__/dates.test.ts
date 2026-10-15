// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"

import { formatDate, formatDateOrRaw, LONG_DATE, MEDIUM_DATE, NUMERIC_DATE } from "@/lib/dates"

const ISO = "2026-03-04T12:00:00Z"

describe("formatDate", () => {
  it("matches toLocaleDateString with the same locale and options", () => {
    const d = new Date(ISO)
    expect(formatDate(ISO, MEDIUM_DATE, "en-US")).toBe("Mar 4, 2026")
    expect(formatDate(ISO, LONG_DATE, "en-US")).toBe("March 4, 2026")
    expect(formatDate(ISO)).toBe(d.toLocaleDateString(undefined, MEDIUM_DATE))
    expect(formatDate(ISO, NUMERIC_DATE)).toBe(d.toLocaleDateString())
  })
})

describe("formatDateOrRaw", () => {
  it("shows a value that is not a date as given", () => {
    expect(formatDateOrRaw("not a date")).toBe("not a date")
    expect(formatDateOrRaw(ISO, MEDIUM_DATE, "en-US")).toBe("Mar 4, 2026")
  })
})
