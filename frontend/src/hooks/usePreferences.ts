// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  getPreferences,
  savePreferences,
  type UserPreferences,
} from "@/lib/api/users"

/** Detect browser timezone for auto-populating user preferences. */
export function detectBrowserTimezone(): string {
  if (typeof window === "undefined") return "America/New_York"
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "America/New_York"
}
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthQuery, useAuthMutation } from "./useAuthQuery"

export function usePreferences(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.user.preferences(),
    queryFn: () => getPreferences(token),
    staleTime: 5 * 60 * 1000,
  })
}

export function useSavePreferences(token?: string) {
  return useAuthMutation({
    mutationFn: (prefs: UserPreferences) => savePreferences(prefs, token),
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
