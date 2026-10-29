// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"

import {
  browserTimeZone,
  civilDate,
  clockToMinutes,
  formatCivilDate,
  formatDate,
  formatDateOrRaw,
  LONG_DATE,
  MEDIUM_DATE,
  NUMERIC_DATE,
  todayCivilDate,
} from "@/lib/dates"

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

describe("formatCivilDate", () => {
  it("shows the date named, not the UTC instant shifted into the local zone", () => {
    expect(formatCivilDate("2026-01-01", MEDIUM_DATE, "en-US")).toBe("Jan 1, 2026")
    expect(formatCivilDate("2026-03-04T00:00:00", LONG_DATE, "en-US")).toBe("March 4, 2026")
  })

  it("shows a value that is not a civil date as given", () => {
    expect(formatCivilDate("soon")).toBe("soon")
  })
})

describe("civilDate", () => {
  it("is the date in the given zone, ISO-shaped", () => {
    const at = new Date("2026-03-04T03:00:00Z")
    expect(civilDate(at, "UTC")).toBe("2026-03-04")
    expect(civilDate(at, "America/Los_Angeles")).toBe("2026-03-03")
  })

  it("defaults to the browser's zone", () => {
    const now = new Date()
    const pad = (n: number) => String(n).padStart(2, "0")
    expect(todayCivilDate()).toBe(
      `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
    )
  })
})

describe("clockToMinutes", () => {
  it("reads HH:MM and H:MM", () => {
    expect(clockToMinutes("09:30")).toBe(570)
    expect(clockToMinutes("9:30:00")).toBe(570)
    expect(clockToMinutes("00:00")).toBe(0)
  })

  it("is null for anything that is not a clock time", () => {
    expect(clockToMinutes("noon")).toBeNull()
    expect(clockToMinutes(undefined)).toBeNull()
  })
})

describe("browserTimeZone", () => {
  it("is the zone Intl resolves", () => {
    expect(browserTimeZone()).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone)
  })
})
