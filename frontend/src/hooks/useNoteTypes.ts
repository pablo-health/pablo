// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { getNoteType, listNoteTypes } from "@/lib/api/noteTypes"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthQuery } from "./useAuthQuery"

const CATALOG_STALE_MS = 5 * 60 * 1000

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
    staleTime: CATALOG_STALE_MS,
  })
}

/**
 * Fetch one note-type definition at the version a note was written against.
 * A given (key, version) never changes, so it is never refetched; a null
 * version means "latest" and follows the catalog's staleTime.
 */
export function useNoteType(key: string, version?: number | null, token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.noteTypes.detail(key, version),
    queryFn: () => getNoteType(key, version, token),
    staleTime: version != null ? Infinity : CATALOG_STALE_MS,
    enabled: !!key,
  })
}

/**
 * Display label for a note-type key, from the catalog. Falls back to the key
 * while the catalog loads, and for a type the catalog no longer lists.
 */
export function useNoteTypeLabel(): (key: string) => string {
  const { data } = useNoteTypes()
  const labels = new Map((data?.note_types ?? []).map((t) => [t.key, t.label]))
  return (key) => labels.get(key) ?? key
}
