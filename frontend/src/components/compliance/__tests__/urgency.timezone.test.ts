// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A due date is a calendar date, so "due in N days" counts the reader's
 * calendar days, from the reader's today, whatever the hour.
 *
 * The count used to read the date as UTC midnight and round the gap to the
 * reader's midnight. West of UTC that rounds to the right day; from 13 hours
 * east (New Zealand in summer, Samoa, Tonga) it is more than half a day off
 * and every due date read one day further away than it is.
 */

import { afterEach, describe, expect, it, vi } from "vitest"
import { daysUntil } from "../urgency"

const ORIGINAL_TZ = process.env.TZ

function at(timeZone: string, instant: string): void {
  process.env.TZ = timeZone
  vi.useFakeTimers()
  vi.setSystemTime(new Date(instant))
}

describe("daysUntil counts calendar days where the reader is", () => {
  afterEach(() => {
    vi.useRealTimers()
    process.env.TZ = ORIGINAL_TZ
  })

  it("in New York just after UTC midnight, while it is still the evening before", () => {
    // 00:13 UTC on the 18th is 20:13 on the 17th in New York.
    at("America/New_York", "2026-10-18T00:13:00Z")
    expect(daysUntil("2026-10-22")).toBe(5)
    expect(daysUntil("2026-10-17")).toBe(0)
  })

  it("in Auckland in summer, thirteen hours ahead of UTC", () => {
    // 12:00 UTC on 15 January is 01:00 on the 16th in Auckland.
    at("Pacific/Auckland", "2026-01-15T12:00:00Z")
    expect(daysUntil("2026-01-21")).toBe(5)
    expect(daysUntil("2026-01-16")).toBe(0)
    expect(daysUntil("2026-01-15")).toBe(-1)
  })

  it("across a daylight-saving change", () => {
    // US clocks go back on 1 November 2026; that week has a 25-hour day.
    at("America/New_York", "2026-10-30T15:00:00Z")
    expect(daysUntil("2026-11-06")).toBe(7)
  })

  it("for a timestamp, by the calendar date it falls on locally", () => {
    at("America/New_York", "2026-10-18T00:13:00Z")
    expect(daysUntil("2026-10-19T15:00:00Z")).toBe(2)
  })
})
