// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

export interface CalendarRangeActionsProps {
  /** First selected day, `YYYY-MM-DD`. */
  startDate: string
  /** Last selected day, included. The same as `startDate` for one day. */
  endDate: string
  /** Let go of the selection, e.g. once an action has run. */
  onDone: () => void
}

/**
 * Extension slot for actions on a selected date range.
 *
 * Rendered inside the bar that appears when a day is selected or a range of
 * days is dragged across on the calendar. Returns null by default; a
 * downstream build may replace this file to offer actions on the selected
 * dates.
 *
 * Contract for replacements: this renders beneath the root providers (React
 * Query and auth context are in scope), once per selection. Render inline
 * controls that fit a single row — buttons, or a menu — and keep its tests in
 * the replacing build.
 */
export function CalendarRangeActions(_props: CalendarRangeActionsProps) {
  return null
}
