// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"

import { formatAgo } from "../format"

const NOW = Date.parse("2026-09-10T12:00:00Z")
const before = (ms: number) => new Date(NOW - ms).toISOString()
const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

describe("formatAgo", () => {
  it.each([
    [0, "just now"],
    [59_999, "just now"],
    [MINUTE, "1 minute ago"],
    [59 * MINUTE, "59 minutes ago"],
    [HOUR, "1 hour ago"],
    [23 * HOUR + 59 * MINUTE, "23 hours ago"],
    [DAY, "1 day ago"],
    [7 * DAY - 1, "6 days ago"],
    [7 * DAY, "on Sep 3, 2026"],
  ])("%d ms ago reads %s", (ms, text) => {
    expect(formatAgo(before(ms), NOW)).toBe(text)
  })

  it("reads a time a little ahead of this clock as just now", () => {
    expect(formatAgo(new Date(NOW + 5_000).toISOString(), NOW)).toBe("just now")
  })
})
