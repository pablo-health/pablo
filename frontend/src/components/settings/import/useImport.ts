// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Data hooks for the import screen. A run is polled while the server is
 * working on it (reading the archive, landing it) and left alone otherwise;
 * history is refreshed whenever a run changes state.
 */

import { useQueryClient } from "@tanstack/react-query"
import { useAuthQuery } from "@/hooks/useAuthQuery"
import {
  IMPORT_BUSY_STATES,
  getImportRun,
  listImportRuns,
  type ImportRunDetail,
} from "@/lib/api/migration"

export const importKeys = {
  all: ["migration"] as const,
  runs: () => ["migration", "runs"] as const,
  run: (id: string) => ["migration", "run", id] as const,
}

/** How often a working run is re-read, and for how long before giving up. */
const POLL_MS = 1500
const POLL_LIMIT = 400

export function useImportRun(runId: string | null) {
  const queryClient = useQueryClient()
  return useAuthQuery<ImportRunDetail>({
    queryKey: importKeys.run(runId ?? "none"),
    queryFn: async () => {
      const run = await getImportRun(runId as string)
      if (!IMPORT_BUSY_STATES.has(run.state)) {
        void queryClient.invalidateQueries({ queryKey: importKeys.runs() })
      }
      return run
    },
    enabled: runId !== null,
    refetchInterval: (query) => {
      const state = query.state.data?.state
      if (!state || !IMPORT_BUSY_STATES.has(state)) return false
      if (query.state.dataUpdateCount > POLL_LIMIT) return false
      return POLL_MS
    },
  })
}

export function useImportHistory() {
  return useAuthQuery({ queryKey: importKeys.runs(), queryFn: listImportRuns })
}
