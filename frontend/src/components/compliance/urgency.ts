// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ComplianceItem, ComplianceTemplate } from "@/types/compliance"

export type Urgency = "overdue" | "due-soon" | "upcoming" | "informational"

const MS_PER_DAY = 24 * 60 * 60 * 1000

export function daysUntil(isoDate: string | null): number | null {
  if (!isoDate) return null
  const target = new Date(isoDate).getTime()
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((target - today.getTime()) / MS_PER_DAY)
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
