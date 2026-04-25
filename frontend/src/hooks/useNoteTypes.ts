// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { listNoteTypes } from "@/lib/api/noteTypes"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthQuery } from "./useAuthQuery"

/**
 * Fetch the registered note-type catalog from the backend.
 *
 * The catalog rarely changes (driven by registry registrations at
 * server startup), so a 5-minute staleTime is plenty.
 */
export function useNoteTypes(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.noteTypes.list(),
    queryFn: () => listNoteTypes(token),
    staleTime: 5 * 60 * 1000,
  })
}
