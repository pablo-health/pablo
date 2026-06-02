// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect } from "vitest"
import {
  DAY_END_HOUR,
  DAY_START_HOUR,
  HOUR_ROW_PX,
  dynamicDayWindow,
  gridHours,
  monthGridDays,
  rangeLabel,
  shiftAnchor,
  visibleRange,
  weekDays,
} from "../dateUtils"

describe("dateUtils", () => {
  it("weekDays returns 7 days starting Sunday", () => {
    // Wednesday May 13, 2026 -> week is Sun May 10 .. Sat May 16
    const wed = new Date(2026, 4, 13)
    const days = weekDays(wed)
    expect(days).toHaveLength(7)
    expect(days[0].getDay()).toBe(0)
    expect(days[0].getDate()).toBe(10)
    expect(days[6].getDate()).toBe(16)
  })

  it("monthGridDays returns 42 cells", () => {
    const may = new Date(2026, 4, 1)
    const days = monthGridDays(may)
    expect(days).toHaveLength(42)
  })

  it("rangeLabel produces editorial header for week within one month", () => {
    const wed = new Date(2026, 4, 13)
    const { primary, secondary } = rangeLabel("week", wed)
    expect(primary).toMatch(/May 10\s+–\s+16/)
    expect(secondary).toBe("2026")
  })

  it("rangeLabel for week spanning two months keeps both month names", () => {
    const lateApr = new Date(2026, 3, 30) // Thursday Apr 30 2026
    const { primary } = rangeLabel("week", lateApr)
    expect(primary).toContain("Apr")
    expect(primary).toContain("May")
  })

  it("rangeLabel for month view shows month + year split", () => {
    const may = new Date(2026, 4, 13)
    const { primary, secondary } = rangeLabel("month", may)
    expect(primary).toBe("May")
    expect(secondary).toBe("2026")
  })

  it("HOUR_ROW_PX is the compact 54px default (must match --ed-row-h)", () => {
    expect(HOUR_ROW_PX).toBe(54)
  })

  it("working-hours window defaults to 7am–8pm", () => {
    expect(DAY_START_HOUR).toBe(7)
    expect(DAY_END_HOUR).toBe(20)
  })

  it("gridHours renders only the working-hours window", () => {
    const hours = gridHours()
    expect(hours[0]).toBe(7)
    expect(hours[hours.length - 1]).toBe(19) // dayEnd - 1
    expect(hours).toHaveLength(DAY_END_HOUR - DAY_START_HOUR)
  })

  it("gridHours supports a full-day window (0–24)", () => {
    const hours = gridHours(0, 24)
    expect(hours).toHaveLength(24)
    expect(hours[0]).toBe(0)
    expect(hours[23]).toBe(23)
  })

  it("shiftAnchor moves by one unit", () => {
    const day = new Date(2026, 4, 13)
    expect(shiftAnchor("day", day, 1).getDate()).toBe(14)
    expect(shiftAnchor("week", day, -1).getDate()).toBe(6)
    expect(shiftAnchor("month", day, 1).getMonth()).toBe(5)
  })

  it("dynamicDayWindow falls back to 7–20 with no appointments", () => {
    const { dayStart, dayEnd } = dynamicDayWindow([])
    expect(dayStart).toBe(DAY_START_HOUR)
    expect(dayEnd).toBe(DAY_END_HOUR)
  })

  it("dynamicDayWindow expands start when an early appointment is present", () => {
    // A 6:00–6:50 appointment should expand dayStart from 7 to 6.
    const appts = [
      { start_at: "2026-06-01T06:00:00", end_at: "2026-06-01T06:50:00" },
    ]
    const { dayStart, dayEnd } = dynamicDayWindow(appts)
    expect(dayStart).toBe(6)
    expect(dayEnd).toBe(DAY_END_HOUR) // default end unchanged
  })

  it("dynamicDayWindow expands end when a late appointment is present", () => {
    // A 21:00–22:00 appointment should expand dayEnd from 20 to 22.
    const appts = [
      { start_at: "2026-06-01T21:00:00", end_at: "2026-06-01T22:00:00" },
    ]
    const { dayStart, dayEnd } = dynamicDayWindow(appts)
    expect(dayStart).toBe(DAY_START_HOUR) // default start unchanged
    expect(dayEnd).toBe(22)
  })

  it("dynamicDayWindow keeps defaults when all appointments fit in 7–20", () => {
    const appts = [
      { start_at: "2026-06-01T09:00:00", end_at: "2026-06-01T09:50:00" },
    ]
    const { dayStart, dayEnd } = dynamicDayWindow(appts)
    expect(dayStart).toBe(DAY_START_HOUR)
    expect(dayEnd).toBe(DAY_END_HOUR)
  })

  it("visibleRange spans 1/7/42 days respectively", () => {
    const day = new Date(2026, 4, 13)
    const dayDelta = visibleRange("day", day).end.getTime() - visibleRange("day", day).start.getTime()
    const weekDelta = visibleRange("week", day).end.getTime() - visibleRange("week", day).start.getTime()
    const monthDelta = visibleRange("month", day).end.getTime() - visibleRange("month", day).start.getTime()
    const dayMs = 86_400_000
    expect(Math.round(dayDelta / dayMs)).toBe(1)
    expect(Math.round(weekDelta / dayMs)).toBe(7)
    // month grid is always 6 weeks = 42 days
    expect(Math.round(monthDelta / dayMs)).toBe(42)
  })
})
