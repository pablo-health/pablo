// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import {
  addDays,
  addMonths,
  addWeeks,
  endOfMonth,
  endOfWeek,
  format,
  isSameDay,
  isSameMonth,
  isToday,
  startOfDay,
  startOfMonth,
  startOfWeek,
} from "date-fns"

export type EditorialView = "day" | "week" | "month"

export const HOUR_ROW_PX = 72

export function weekDays(anchor: Date): Date[] {
  const start = startOfWeek(anchor, { weekStartsOn: 0 })
  return Array.from({ length: 7 }, (_, i) => addDays(start, i))
}

export function monthGridDays(anchor: Date): Date[] {
  const start = startOfWeek(startOfMonth(anchor), { weekStartsOn: 0 })
  const end = endOfWeek(endOfMonth(anchor), { weekStartsOn: 0 })
  const days: Date[] = []
  let cursor = start
  while (cursor <= end) {
    days.push(cursor)
    cursor = addDays(cursor, 1)
  }
  while (days.length < 42) days.push(addDays(days[days.length - 1], 1))
  return days.slice(0, 42)
}

export function visibleRange(view: EditorialView, anchor: Date): { start: Date; end: Date } {
  if (view === "day") {
    const start = startOfDay(anchor)
    return { start, end: addDays(start, 1) }
  }
  if (view === "week") {
    const start = startOfWeek(anchor, { weekStartsOn: 0 })
    return { start, end: addDays(start, 7) }
  }
  // Month grid is always 6 weeks = 42 days from the leading Sunday.
  const start = startOfWeek(startOfMonth(anchor), { weekStartsOn: 0 })
  return { start, end: addDays(start, 42) }
}

export function shiftAnchor(view: EditorialView, anchor: Date, dir: -1 | 1): Date {
  if (view === "day") return addDays(anchor, dir)
  if (view === "week") return addWeeks(anchor, dir)
  return addMonths(anchor, dir)
}

export function rangeLabel(view: EditorialView, anchor: Date): { primary: string; secondary: string } {
  if (view === "day") {
    return { primary: format(anchor, "EEEE, MMM d"), secondary: format(anchor, "yyyy") }
  }
  if (view === "week") {
    const start = startOfWeek(anchor, { weekStartsOn: 0 })
    const end = addDays(start, 6)
    const sameMonth = isSameMonth(start, end)
    const primary = sameMonth
      ? `${format(start, "MMM d")} – ${format(end, "d")}`
      : `${format(start, "MMM d")} – ${format(end, "MMM d")}`
    return { primary, secondary: format(end, "yyyy") }
  }
  return { primary: format(anchor, "MMMM"), secondary: format(anchor, "yyyy") }
}

/** Minutes since midnight in the viewer's local timezone. */
export function minutesSinceMidnight(iso: string): number {
  const d = new Date(iso)
  return d.getHours() * 60 + d.getMinutes()
}

export { addDays, format, isSameDay, isSameMonth, isToday, startOfDay, startOfMonth, startOfWeek }
