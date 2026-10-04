// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { offHoursGaps, type MinuteRange } from "./schedule"

interface ScheduleLayerProps {
  /** This day's working time, in minutes since midnight. Null when the
   * practice has no rules at all, which is never drawn as time off. */
  working: MinuteRange[] | null
  /** Minutes since midnight up to which this day is already past. */
  pastUntil: number
  dayStartHour: number
  dayEndHour: number
  rowHeightPx: number
}

/**
 * Background for one day of the grid: time outside working hours hatched,
 * and time already gone washed over, so a past working day still reads as a
 * working day. Always `pointer-events: none` and unstacked (no explicit
 * z-index) so it paints behind sibling event cards (which set `z-10`) and
 * never intercepts the slot-click handler on the canvas beneath it.
 */
export function ScheduleLayer({
  working,
  pastUntil,
  dayStartHour,
  dayEndHour,
  rowHeightPx,
}: ScheduleLayerProps) {
  const windowStart = dayStartHour * 60
  const windowEnd = dayEndHour * 60
  const top = (minute: number) => ((minute - windowStart) / 60) * rowHeightPx
  const gaps = working ? offHoursGaps(working, dayStartHour, dayEndHour) : []
  const pastEnd = Math.min(pastUntil, windowEnd)
  return (
    <>
      {gaps.map((gap) => (
        <div
          key={gap.startMin}
          aria-hidden
          className="ed-unavailable pointer-events-none absolute left-0 right-0"
          style={{ top: top(gap.startMin), height: top(gap.endMin) - top(gap.startMin) }}
        />
      ))}
      {pastEnd > windowStart ? (
        <div
          aria-hidden
          data-testid="past-time"
          className="ed-past pointer-events-none absolute left-0 right-0"
          style={{ top: 0, height: top(pastEnd) }}
        />
      ) : null}
    </>
  )
}
