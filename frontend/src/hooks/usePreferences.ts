// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  getPreferences,
  savePreferences,
  type UserPreferences,
} from "@/lib/api/users"
import { queryKeys } from "@/lib/api/queryKeys"

export function usePreferences(token?: string) {
  return useQuery({
    queryKey: queryKeys.user.preferences(),
    queryFn: () => getPreferences(token),
    staleTime: 5 * 60 * 1000,
  })
}

export function useSavePreferences(token?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (prefs: UserPreferences) => savePreferences(prefs, token),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.user.preferences(), data)
    },
  })
}
