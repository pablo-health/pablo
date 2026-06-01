// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect } from "vitest"
import { rescheduledStart, snapDragDelta } from "../dragSnap"

const ROW_PX = 54 // matches HOUR_ROW_PX
const COL_PX = 120

describe("snapDragDelta — vertical 15-minute snap", () => {
  it("snaps no movement to zero", () => {
    expect(snapDragDelta(0, 0, ROW_PX, COL_PX, "day")).toEqual({
      minuteShift: 0,
      dayShift: 0,
    })
  })

  it("snaps a quarter-row drag down to +15 minutes", () => {
    // 15 min = a quarter of a 60-min row.
    const dy = ROW_PX / 4
    expect(snapDragDelta(0, dy, ROW_PX, COL_PX, "day").minuteShift).toBe(15)
  })

  it("snaps a full-row drag down to +60 minutes", () => {
    expect(snapDragDelta(0, ROW_PX, ROW_PX, COL_PX, "day").minuteShift).toBe(60)
  })

  it("snaps an upward drag to a negative multiple of 15", () => {
    const dy = -(ROW_PX / 2) // half a row up = -30 min
    expect(snapDragDelta(0, dy, ROW_PX, COL_PX, "day").minuteShift).toBe(-30)
  })

  it("rounds a near-quarter drag to the closest 15-minute step", () => {
    // ~10 px ≈ 11 min → rounds to 15.
    const dy = (11 / 60) * ROW_PX
    expect(snapDragDelta(0, dy, ROW_PX, COL_PX, "day").minuteShift).toBe(15)
  })
})

describe("snapDragDelta — horizontal column snap", () => {
  it("shifts one column right in week mode at one column width", () => {
    expect(snapDragDelta(COL_PX, 0, ROW_PX, COL_PX, "week").dayShift).toBe(1)
  })

  it("rounds to the nearest column", () => {
    expect(
      snapDragDelta(COL_PX * 1.6, 0, ROW_PX, COL_PX, "week").dayShift,
    ).toBe(2)
    expect(
      snapDragDelta(-COL_PX * 0.6, 0, ROW_PX, COL_PX, "week").dayShift,
    ).toBe(-1)
  })

  it("never shifts days in day mode", () => {
    expect(snapDragDelta(COL_PX * 3, 0, ROW_PX, COL_PX, "day").dayShift).toBe(0)
  })

  it("combines vertical and horizontal snaps in week mode", () => {
    const result = snapDragDelta(COL_PX, ROW_PX / 4, ROW_PX, COL_PX, "week")
    expect(result).toEqual({ minuteShift: 15, dayShift: 1 })
  })
})

describe("rescheduledStart — preserves duration & clamps to the day", () => {
  it("shifts the start by the snapped minute delta", () => {
    const start = new Date(2026, 5, 1, 9, 0, 0)
    const out = new Date(rescheduledStart(start.toISOString(), 50, 15, 0))
    expect(out.getHours()).toBe(9)
    expect(out.getMinutes()).toBe(15)
  })

  it("shifts the day by the snapped day delta keeping the time", () => {
    const start = new Date(2026, 5, 1, 9, 0, 0)
    const out = new Date(rescheduledStart(start.toISOString(), 50, 0, 2))
    expect(out.getDate()).toBe(3)
    expect(out.getHours()).toBe(9)
  })

  it("clamps within the landing day so the end never spills past midnight", () => {
    // Drag a 60-min appt 30 min later from 23:00 → 23:30 would end at 00:30;
    // clamp keeps the start so the whole appt fits before midnight (23:00).
    const start = new Date(2026, 5, 1, 23, 0, 0)
    const out = new Date(rescheduledStart(start.toISOString(), 60, 30, 0))
    expect(out.getDate()).toBe(1)
    expect(out.getHours()).toBe(23)
    expect(out.getMinutes()).toBe(0)
  })

  it("clamps a drag above the top of the day to midnight", () => {
    // Small upward drag from 00:30 past the top clamps the start to 00:00.
    const start = new Date(2026, 5, 1, 0, 30, 0)
    const out = new Date(rescheduledStart(start.toISOString(), 50, -45, 0))
    expect(out.getDate()).toBe(1)
    expect(out.getHours()).toBe(0)
    expect(out.getMinutes()).toBe(0)
  })
})
