// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useQuery } from "@tanstack/react-query"
import { useAuth } from "@/lib/auth-context"
import { ApiError } from "@/lib/api/client"
import { listCompanionDevices, type CompanionDevice } from "@/lib/api/devices"
import { queryKeys } from "@/lib/api/queryKeys"

/**
 * List the current user's enrolled companion installs.
 *
 * Used by the dashboard for smart-detection of the "Start Session" handoff
 * button. A 404 means the backend has no devices endpoint (an older
 * self-hosted backend), which resolves to an empty list so the caller shows
 * the "Download Pablo Companion" affordance.
 *
 * Any other failure (401, 5xx, network) is surfaced as a query error. It must
 * not read as "no devices": that would tell a clinician who has the app to go
 * download it again. Callers check `isError` and offer a retry instead.
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
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return []
        throw err
      }
    },
    staleTime: 10 * 1000,
    enabled: !loading,
    retry: false,
  })
}
