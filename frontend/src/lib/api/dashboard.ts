// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { DashboardSummary } from "@/types/dashboard"
import { get } from "./client"

export interface DashboardRanges {
  today_start: string
  today_end: string
  week_start: string
  week_end: string
}

export async function getDashboardSummary(
  ranges: DashboardRanges,
  token?: string,
): Promise<DashboardSummary> {
  const params = new URLSearchParams(Object.entries(ranges))
  return get<DashboardSummary>(`/api/dashboard/summary?${params}`, token)
}
