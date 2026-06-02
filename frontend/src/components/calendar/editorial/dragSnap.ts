// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** Minimum pointer travel (px) before a press becomes a drag, so plain
 * clicks and double-clicks still register on the event card. */
export const DRAG_THRESHOLD_PX = 5

/** Vertical snap granularity for rescheduling, in minutes. */
export const SNAP_MINUTES = 15

/** Minutes-per-day, used when clamping a rescheduled start within its day. */
const MINUTES_PER_DAY = 24 * 60

/**
 * Snap a raw pointer delta to the calendar grid.
 *
 * Vertical travel maps to {@link SNAP_MINUTES}-minute steps (one hour row =
 * `rowHeightPx`); horizontal travel maps to whole-column (day) steps in week
 * mode only. Day mode never shifts days.
 *
 * @param sourceDayIndex - 0-based index (0–6) of the source day within the
 *   visible week. Passing it allows callers to clamp the returned
 *   {@link dayShift} so the target column stays within the visible grid.
 *   Omit (or pass `undefined`) to skip clamping.
 */
export function snapDragDelta(
  dx: number,
  dy: number,
  rowHeightPx: number,
  colWidthPx: number,
  mode: "week" | "day",
  sourceDayIndex?: number,
): { minuteShift: number; dayShift: number } {
  const rawMinutes = (dy / rowHeightPx) * 60
  const minuteShift = Math.round(rawMinutes / SNAP_MINUTES) * SNAP_MINUTES
  let dayShift =
    mode === "week" && colWidthPx > 0 ? Math.round(dx / colWidthPx) : 0
  // Clamp the target column to [0, 6] so a wide drag can't land outside the
  // visible week.
  if (mode === "week" && sourceDayIndex !== undefined) {
    const targetIdx = sourceDayIndex + dayShift
    const clampedIdx = Math.max(0, Math.min(6, targetIdx))
    dayShift = clampedIdx - sourceDayIndex
  }
  return { minuteShift, dayShift }
}

/**
 * Compute the rescheduled start ISO string from a snapped delta, keeping the
 * original duration and clamping the start so the whole appointment stays
 * within its target day (no rollover past midnight at either edge).
 *
 * The horizontal {@link dayShift} chooses the target day using calendar
 * arithmetic (DST-safe — no fixed 86_400_000 ms addition); the vertical
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
  // Use calendar arithmetic to shift the day (DST-correct: setDate advances by
  // one calendar day regardless of whether a DST transition lengthens or
  // shortens that day). setMinutes with a 0–1439 value normalises into hours.
  const target = new Date(original)
  target.setHours(0, 0, 0, 0)
  target.setDate(target.getDate() + dayShift)
  target.setMinutes(clamped)
  return target.toISOString()
}
