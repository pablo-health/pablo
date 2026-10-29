// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  getPreferences,
  updatePreferences,
  type PreferenceChanges,
} from "@/lib/api/users"
import { queryKeys } from "@/lib/api/queryKeys"
import { browserTimeZone } from "@/lib/dates"
import { useAuthQuery, useAuthMutation } from "./useAuthQuery"

/** Detect browser timezone for auto-populating user preferences. */
export function detectBrowserTimezone(): string {
  return typeof window === "undefined" ? "America/New_York" : browserTimeZone()
}

export function usePreferences(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.user.preferences(),
    queryFn: () => getPreferences(token),
    staleTime: 5 * 60 * 1000,
  })
}

/**
 * Save some preferences. Pass only the keys being changed — see
 * `updatePreferences` for why a full copy must never be sent.
 */
export function useUpdatePreferences(token?: string) {
  return useAuthMutation({
    mutationFn: (changes: PreferenceChanges) => updatePreferences(changes, token),
    onSuccess: (data, _variables, queryClient) => {
      queryClient.setQueryData(queryKeys.user.preferences(), data)
    },
  })
}

/**
 * The IANA timezone to render dates/times in. Prefers the clinician's saved
 * preference and falls back to the browser's detected zone. Use this anywhere
 * the UI formats a date so every surface agrees on which day it is — see
 * `formatInUserTimeZone`.
 */
export function useUserTimeZone(token?: string): string {
  const { data } = usePreferences(token)
  return data?.timezone || detectBrowserTimezone()
}

/** Format a date in the user's timezone (see `useUserTimeZone`). */
export function formatInUserTimeZone(
  date: Date | string,
  timeZone: string,
  options: Intl.DateTimeFormatOptions,
): string {
  return new Date(date).toLocaleDateString("en-US", { ...options, timeZone })
}
