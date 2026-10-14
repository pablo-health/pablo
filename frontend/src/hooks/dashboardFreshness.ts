// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { QueryClient } from "@tanstack/react-query"
import { queryKeys } from "@/lib/api/queryKeys"

type QueryKeyList = readonly (readonly unknown[])[]

/**
 * How the dashboard stays current after the clinician changes something.
 *
 * Every dashboard panel reads one cached query (`useDashboardSummary`), built
 * server-side from appointments (today's list, the week's count), sessions
 * (transcribing, awaiting review) and session notes (awaiting signature). It
 * lives under its own key, so invalidating appointments or sessions does not
 * reach it, and with a minute of staleTime and no refetch on focus a new
 * appointment stayed off the dashboard until a reload.
 *
 * So anything that changes one of those adds the dashboard to what it
 * invalidates, through `withDashboard` (mutation `invalidateKeys`) or
 * `refreshDashboard` (a direct `invalidateQueries`). The prefix key covers the
 * summary for every day/week range it has been cached under.
 */
export function withDashboard(keys: QueryKeyList): QueryKeyList {
  return [...keys, queryKeys.dashboard.all]
}

export function refreshDashboard(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.all })
}
