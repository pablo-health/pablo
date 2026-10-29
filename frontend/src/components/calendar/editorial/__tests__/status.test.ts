// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { calendarShowing, heldLabel } from "../status"

// Monday 26 Oct 2026, mid-morning local time.
const NOW = new Date(2026, 9, 26, 10, 0)

describe("heldLabel", () => {
  it("names the weekday when the hold lapses within the week", () => {
    expect(heldLabel(new Date(2026, 9, 30, 17, 0).toISOString(), NOW)).toBe("Held until Fri")
  })

  it("names the time when it lapses today", () => {
    expect(heldLabel(new Date(2026, 9, 26, 17, 0).toISOString(), NOW)).toBe(
      "Held until 5:00 PM",
    )
  })

  it("names the date when it is a week or more away", () => {
    expect(heldLabel(new Date(2026, 10, 4, 9, 0).toISOString(), NOW)).toBe("Held until Nov 4")
  })

  it("says only Held when the expiry is unknown", () => {
    expect(heldLabel(null, NOW)).toBe("Held")
  })
})

describe("calendarShowing", () => {
  it("shows a skipped cancellation as skipped, and any other row as its status", () => {
    expect(calendarShowing({ status: "cancelled", skipped: true })).toBe("skipped")
    expect(calendarShowing({ status: "cancelled", skipped: false })).toBe("cancelled")
    expect(calendarShowing({ status: "cancelled" })).toBe("cancelled")
    expect(calendarShowing({ status: "pending" })).toBe("pending")
  })
})
