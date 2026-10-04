// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * How the calendar's own queries stay current with the server.
 *
 * Calendar reads (the scheduled pass, or one asked for) can move, add or
 * cancel sessions while the calendar is open, and nothing pushes that to the
 * page. The app-wide defaults (`components/providers.tsx`) keep data for a
 * minute and skip refetching on focus, which suits most screens and left the
 * calendar showing an old schedule until a reload. So the calendar queries
 * opt in to two things here, leaving the defaults alone:
 *
 * - Refetch whenever the tab regains focus, even inside the stale window:
 *   the typical case is a clinician moving a session in their calendar app
 *   and switching straight back.
 * - Refetch every minute while the calendar is mounted. React Query pauses
 *   the interval while the tab is hidden (`refetchIntervalInBackground`
 *   false) and drops it when the calendar unmounts.
 */
export const CALENDAR_REFETCH_INTERVAL_MS = 60 * 1000

export const calendarFreshness = {
  refetchOnWindowFocus: "always",
  refetchInterval: CALENDAR_REFETCH_INTERVAL_MS,
  refetchIntervalInBackground: false,
} as const
