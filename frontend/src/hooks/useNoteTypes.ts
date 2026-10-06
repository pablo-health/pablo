// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  deriveNoteType,
  getNoteType,
  listDeriveReferences,
  listNoteTypes,
  previewNoteDraft,
  retirePracticeNoteType,
  savePracticeNoteType,
} from "@/lib/api/noteTypes"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  DeriveNoteTypeRequest,
  DeriveNoteTypeResponse,
  NoteDraftPreviewRequest,
  NoteDraftPreviewResponse,
  NoteTypeSchema,
  PracticeNoteTypeSpec,
} from "@/types/noteTypes"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

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

/** Save the next version of one of the practice's own types. */
export function useSavePracticeNoteType(token?: string) {
  return useAuthMutation<NoteTypeSchema, { slug: string; spec: PracticeNoteTypeSpec }>({
    mutationFn: ({ slug, spec }) => savePracticeNoteType(slug, spec, token),
    invalidateKeys: [queryKeys.noteTypes.all],
  })
}

/** Retire one of the practice's own types. */
export function useRetirePracticeNoteType(token?: string) {
  return useAuthMutation<NoteTypeSchema, string>({
    mutationFn: (slug) => retirePracticeNoteType(slug, token),
    invalidateKeys: [queryKeys.noteTypes.all],
  })
}

/** Draft a note from a transcript without saving it. Writes nothing, so invalidates nothing. */
export function usePreviewNoteDraft(token?: string) {
  return useAuthMutation<NoteDraftPreviewResponse, NoteDraftPreviewRequest>({
    mutationFn: (body) => previewNoteDraft(body, token),
  })
}

/** Propose a note type from the clinician's notes. Saves nothing, so invalidates nothing. */
export function useDeriveNoteType(token?: string) {
  return useAuthMutation<DeriveNoteTypeResponse, DeriveNoteTypeRequest>({
    mutationFn: (request) => deriveNoteType(request, token),
  })
}

/** References registered with this deployment; fixed at server startup. */
export function useDeriveReferences(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.noteTypes.deriveReferences(),
    queryFn: () => listDeriveReferences(token),
    staleTime: Infinity,
  })
}
