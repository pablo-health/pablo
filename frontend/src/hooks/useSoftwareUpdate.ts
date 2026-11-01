// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useState } from "react"
import { queryKeys } from "@/lib/api/queryKeys"
import {
  fetchSoftwareUpdate,
  fetchUpdateStatus,
  type SoftwareUpdate,
} from "@/lib/api/softwareUpdate"
import { useConfig } from "@/lib/config"
import { useAuthQuery } from "./useAuthQuery"

const POLL_MS = 5_000
// Past this, stop asking and say so: a backup of a large practice plus a
// rollback can run long, but not this long.
const GIVE_UP_MS = 20 * 60_000

/**
 * The running and the latest release, asked once per page load. A 404 means
 * this deployment does not update itself: the query fails, is not retried,
 * and nothing asks again.
 */
export function useSoftwareUpdate(enabled: boolean) {
  const { dataMode } = useConfig()
  return useAuthQuery<SoftwareUpdate>({
    queryKey: queryKeys.softwareUpdate.all,
    queryFn: fetchSoftwareUpdate,
    enabled: enabled && dataMode !== "mock",
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
}

export type UpdateOutcome = "updating" | "done" | "rolled_back" | "failed" | "timed_out"

/**
 * Follows an update to `target` until it is known to have finished.
 *
 * Reads the host's status file, which answers while the backend restarts,
 * and the running version from the API, which answers once it is back. Done
 * means both: the host says it checked the new release, and the API says it
 * runs it. A status file written before this update (its `at` is still
 * `baselineAt`) is ignored, so the last update's result is not read as this
 * one's.
 */
export function useUpdateProgress(
  target: string | null,
  baselineAt: string | null,
): UpdateOutcome | null {
  const [finished, setFinished] = useState<{ target: string; outcome: UpdateOutcome } | null>(
    null,
  )

  useEffect(() => {
    if (!target) return
    const startedAt = Date.now()
    let stopped = false

    const tick = async () => {
      const status = await fetchUpdateStatus()
      const fresh = status !== null && status.at !== baselineAt ? status : null
      if (fresh?.state === "failed" || fresh?.state === "rolled_back") {
        return fresh.state
      }
      try {
        const running = await fetchSoftwareUpdate()
        // No status file to read (a proxy that does not serve it) leaves the
        // API's answer as the only evidence.
        if (running.current === target && (status === null || fresh?.state === "done")) {
          return "done"
        }
      } catch {
        // The backend is restarting.
      }
      return Date.now() - startedAt > GIVE_UP_MS ? "timed_out" : null
    }

    const timer = setInterval(() => {
      void tick().then((result) => {
        if (result && !stopped) {
          stopped = true
          clearInterval(timer)
          setFinished({ target, outcome: result })
        }
      })
    }, POLL_MS)
    return () => {
      stopped = true
      clearInterval(timer)
    }
  }, [target, baselineAt])

  if (!target) return null
  return finished?.target === target ? finished.outcome : "updating"
}
