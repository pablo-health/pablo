// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { formatFileSize } from "@/lib/utils/fileValidation"

/** "3 files, 1.2 KB" */
export function describeFiles(count: number, bytes: number): string {
  return `${count} ${count === 1 ? "file" : "files"}, ${formatFileSize(bytes)}`
}

/** "Sep 1, 2026, 3:04 PM" */
export function formatWhen(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

/** "Sep 1, 2026" */
export function formatDay(value: string): string {
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
}

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** "just now", "5 minutes ago", "3 hours ago", "2 days ago"; past a week, "on Sep 1, 2026". */
export function formatAgo(value: string, now: number = Date.now()): string {
  const elapsed = Math.max(0, now - new Date(value).getTime())
  if (elapsed < MINUTE) return "just now"
  if (elapsed >= 7 * DAY) return `on ${formatDay(value)}`
  const ago = new Intl.RelativeTimeFormat(undefined, { numeric: "always" })
  if (elapsed < HOUR) return ago.format(-Math.floor(elapsed / MINUTE), "minute")
  if (elapsed < DAY) return ago.format(-Math.floor(elapsed / HOUR), "hour")
  return ago.format(-Math.floor(elapsed / DAY), "day")
}
