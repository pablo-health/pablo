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
