// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Pure helpers behind the "Bring over your week" grid: which weekday+hour
 * cells the calendar shows as busy, and which of those cells a scan's
 * proposed series structurally matches. No event content ever passes
 * through here — a busy window is start/end only, and a series is already
 * reduced to a (weekday, local start) pattern before it reaches this file.
 */

import type { BusyWindow, ProposedSeries } from "@/lib/api/scheduling"

/** Monday through Friday, matching the backend's `weekday()` convention
 * (Monday is 0). The grid only ever shows a business week. */
export const GRID_WEEKDAYS = [0, 1, 2, 3, 4] as const

/** 9am through 4pm — the stretch of a day the illustrative grid covers. */
export const GRID_HOURS = [9, 10, 11, 12, 13, 14, 15, 16] as const

export function cellKey(weekday: number, hour: number): string {
  return `${weekday}-${hour}`
}

/** JS's Date.getDay() is Sunday=0..Saturday=6; the grid speaks Monday=0. */
function mondayZeroWeekday(date: Date): number {
  return (date.getDay() + 6) % 7
}

function inGrid(weekday: number, hour: number): boolean {
  return (
    (GRID_WEEKDAYS as readonly number[]).includes(weekday) &&
    (GRID_HOURS as readonly number[]).includes(hour)
  )
}

/** Weekday+hour cells the calendar shows as busy, restricted to the grid's
 * displayed range. A window spanning several hours contributes one cell
 * per hour it touches, so a 2-hour block reads as 2 anonymous cells. */
export function busyCellKeys(windows: readonly BusyWindow[]): Set<string> {
  const keys = new Set<string>()
  for (const window of windows) {
    const start = new Date(window.start)
    const end = new Date(window.end)
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) continue

    const cursor = new Date(start)
    cursor.setMinutes(0, 0, 0)
    // Cap the walk so a malformed multi-day window can't hang the loop.
    let steps = 0
    while (cursor < end && steps < 24 * 8) {
      const weekday = mondayZeroWeekday(cursor)
      const hour = cursor.getHours()
      if (inGrid(weekday, hour)) keys.add(cellKey(weekday, hour))
      cursor.setHours(cursor.getHours() + 1)
      steps += 1
    }
  }
  return keys
}

/** Weekday+hour cells a scan's proposed series matches. A series IS a
 * (weekday, local start) pattern by construction, so each one names
 * exactly one cell — this is the "sage" set the grid animates into. */
export function seriesCellKeys(series: readonly ProposedSeries[]): Set<string> {
  const keys = new Set<string>()
  for (const item of series) {
    const hour = Number.parseInt(item.local_start_time.split(":")[0] ?? "", 10)
    if (Number.isNaN(hour) || !inGrid(item.weekday, hour)) continue
    keys.add(cellKey(item.weekday, hour))
  }
  return keys
}
