// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useQuery } from "@tanstack/react-query"
import { useAuth } from "@/lib/auth-context"
import { listCompanionDevices, type CompanionDevice } from "@/lib/api/devices"
import { queryKeys } from "@/lib/api/queryKeys"

/**
 * List the current user's enrolled companion installs.
 *
 * Used by the dashboard for smart-detection of the "Start Session" handoff
 * button. Degrades gracefully: if the backend endpoint is unavailable (404
 * while the launch flow is dark, or older self-hosted backends), the query
 * resolves to an empty list rather than surfacing an error — the caller
 * simply renders the "Download Pablo Companion" affordance instead.
 *
 * Shorter `staleTime` than the app default so a freshly-enrolled companion
 * (the user just finished OAuth and landed back on the dashboard) is
 * detected within seconds rather than the default 60s window.
 */
export function useCompanionDevices(token?: string) {
  const { loading } = useAuth()
  return useQuery<CompanionDevice[]>({
    queryKey: queryKeys.user.devices(),
    queryFn: async () => {
      try {
        return await listCompanionDevices(token)
      } catch {
        // No endpoint / flag off / transient error → treat as "no devices".
        return []
      }
    },
    staleTime: 10 * 1000,
    enabled: !loading,
    retry: false,
  })
}
