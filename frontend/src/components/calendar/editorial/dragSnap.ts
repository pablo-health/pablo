// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** Minimum pointer travel (px) before a press becomes a drag, so plain
 * clicks and double-clicks still register on the event card. */
export const DRAG_THRESHOLD_PX = 5

/** Vertical snap granularity for rescheduling, in minutes. */
export const SNAP_MINUTES = 15

/** Minutes-per-day, used when clamping a rescheduled start within its day. */
const MINUTES_PER_DAY = 24 * 60
const MS_PER_MINUTE = 60_000
const MS_PER_DAY = 86_400_000

/**
 * Snap a raw pointer delta to the calendar grid.
 *
 * Vertical travel maps to {@link SNAP_MINUTES}-minute steps (one hour row =
 * `rowHeightPx`); horizontal travel maps to whole-column (day) steps in week
 * mode only. Day mode never shifts days.
 */
export function snapDragDelta(
  dx: number,
  dy: number,
  rowHeightPx: number,
  colWidthPx: number,
  mode: "week" | "day",
): { minuteShift: number; dayShift: number } {
  const rawMinutes = (dy / rowHeightPx) * 60
  const minuteShift = Math.round(rawMinutes / SNAP_MINUTES) * SNAP_MINUTES
  const dayShift =
    mode === "week" && colWidthPx > 0 ? Math.round(dx / colWidthPx) : 0
  return { minuteShift, dayShift }
}

/**
 * Compute the rescheduled start ISO string from a snapped delta, keeping the
 * original duration and clamping the start so the whole appointment stays
 * within its target day (no rollover past midnight at either edge).
 *
 * The horizontal {@link dayShift} chooses the target day; the vertical
 * {@link minuteShift} moves the time, which is then clamped to
 * `[0, 24h − duration]` of that day so a late or early drag never silently
 * spills onto an adjacent date.
 */
export function rescheduledStart(
  startAt: string,
  durationMinutes: number,
  minuteShift: number,
  dayShift: number,
): string {
  const original = new Date(startAt)
  const minuteOfDay = original.getHours() * 60 + original.getMinutes()
  const clamped = Math.max(
    0,
    Math.min(minuteOfDay + minuteShift, MINUTES_PER_DAY - durationMinutes),
  )
  // Anchor to local midnight of the target day, then add the clamped minutes —
  // this keeps the result on the intended date regardless of the drag size.
  const targetMidnight = new Date(original)
  targetMidnight.setHours(0, 0, 0, 0)
  return new Date(
    targetMidnight.getTime() + dayShift * MS_PER_DAY + clamped * MS_PER_MINUTE,
  ).toISOString()
}
