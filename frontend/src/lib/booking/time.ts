// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Wall-clock formatting for the public booking pages
 * (docs/design/public-booking.md). Times are the practice's local
 * wall-clock — the `Z` suffix on slot strings is cosmetic, matching the
 * scheduling engine — so these never do timezone conversion.
 */

/** "2026-08-28T09:30:00Z" -> "9:30 AM" (wall-clock, no timezone math). */
export function slotTimeLabel(slotStart: string): string {
  const [h, min] = slotStart.slice(11, 16).split(":").map(Number)
  const period = h >= 12 ? "PM" : "AM"
  const hour12 = h % 12 === 0 ? 12 : h % 12
  return `${hour12}:${String(min).padStart(2, "0")} ${period}`
}

export function longDateLabel(dateStr: string): string {
  const [y, m, d] = dateStr.split("-").map(Number)
  return new Date(y, m - 1, d).toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  })
}
