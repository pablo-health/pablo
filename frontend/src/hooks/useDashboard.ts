// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { getDashboardSummary } from "@/lib/api/dashboard"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthQuery } from "./useAuthQuery"

/** Local-day bounds: [midnight today, midnight tomorrow). */
function todayBounds(): { start: string; end: string } {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const end = new Date(start)
  end.setDate(end.getDate() + 1)
  return { start: start.toISOString(), end: end.toISOString() }
}

/** Rest-of-week bounds: [midnight today, next Monday midnight). */
function restOfWeekBounds(): { start: string; end: string } {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const end = new Date(start)
  const daysUntilMonday = (8 - start.getDay()) % 7 || 7
  end.setDate(end.getDate() + daysUntilMonday)
  return { start: start.toISOString(), end: end.toISOString() }
}

/**
 * One aggregate read for the whole dashboard. Every panel reads a slice of
 * this single query (via `select`) instead of issuing its own request, so a
 * dashboard load is one backend round-trip and one DB connection rather than
 * the former per-panel fan-out.
 */
export function useDashboardSummary(token?: string) {
  const today = todayBounds()
  const week = restOfWeekBounds()
  return useAuthQuery({
    queryKey: queryKeys.dashboard.summary({ today: today.start, week: week.end }),
    queryFn: () =>
      getDashboardSummary(
        {
          today_start: today.start,
          today_end: today.end,
          week_start: week.start,
          week_end: week.end,
        },
        token,
      ),
    staleTime: 60 * 1000,
  })
}
