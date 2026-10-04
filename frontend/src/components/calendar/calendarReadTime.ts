// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * When a calendar was last read, the way the rest of the app shows a time:
 * "3:42 PM" for today, "Oct 3, 3:42 PM" for an earlier day. `timeZone` is
 * the practice's zone when known, so the time matches the calendar beside it.
 */
export function formatLastRead(iso: string, now: Date = new Date(), timeZone?: string): string {
  const read = new Date(iso)
  if (Number.isNaN(read.getTime())) return ""
  const zone = timeZone ? { timeZone } : {}
  const day = (d: Date) =>
    new Intl.DateTimeFormat("en-US", { year: "numeric", month: "numeric", day: "numeric", ...zone }).format(d)
  const time = read.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", ...zone })
  if (day(read) === day(now)) return time
  const date = read.toLocaleDateString("en-US", { month: "short", day: "numeric", ...zone })
  return `${date}, ${time}`
}

/**
 * The time every calendar has been read since: the oldest of their last
 * reads. Saying the newest would claim a read the others never had.
 * Null when no calendar has been read.
 */
export function oldestRead(times: (string | null | undefined)[]): string | null {
  const known = times.filter((t): t is string => Boolean(t) && !Number.isNaN(new Date(t as string).getTime()))
  if (known.length === 0) return null
  return known.reduce((oldest, t) => (new Date(t) < new Date(oldest) ? t : oldest))
}
