// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ComplianceItem, ComplianceTemplate } from "@/types/compliance"

export type Urgency = "overdue" | "due-soon" | "upcoming" | "informational"

const MS_PER_DAY = 24 * 60 * 60 * 1000

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/

/**
 * Calendar days from the reader's today to `isoDate`.
 *
 * A due date is a calendar date, so it is read as that date where the reader
 * is, not as UTC midnight — read as UTC midnight it lands on the day before
 * west of UTC and more than half a day later from 13 hours east, and the
 * count was a day out there. A timestamp counts by the local date it falls on.
 * Both ends become UTC midnights of their local dates before subtracting, so
 * a 23- or 25-hour day across a clock change still counts as one.
 */
export function daysUntil(isoDate: string | null): number | null {
  if (!isoDate) return null
  const dateOnly = DATE_ONLY.exec(isoDate)
  let target: number
  if (dateOnly) {
    target = Date.UTC(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
  } else {
    const instant = new Date(isoDate)
    if (Number.isNaN(instant.getTime())) return null
    target = Date.UTC(instant.getFullYear(), instant.getMonth(), instant.getDate())
  }
  const now = new Date()
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((target - today) / MS_PER_DAY)
}

export function urgencyFor(
  item: ComplianceItem,
  template: ComplianceTemplate | undefined,
): Urgency {
  if (!template?.reminder_windows.length) return "informational"
  const days = daysUntil(item.due_date)
  if (days === null) return "informational"
  if (days < 0) return "overdue"
  const widest = Math.max(...template.reminder_windows)
  if (days <= widest) return "due-soon"
  return "upcoming"
}

export function formatDueLabel(days: number | null): string {
  if (days === null) return ""
  if (days < 0) return `${Math.abs(days)} days overdue`
  if (days === 0) return "due today"
  if (days === 1) return "due tomorrow"
  return `due in ${days} days`
}
