// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { applyMdmCodes, getMdmReview, putMdmChoices } from "@/lib/api/mdm"
import { queryKeys } from "@/lib/api/queryKeys"
import { offersMdmReview } from "@/lib/mdm"
import { MDM_LEVELS, type MdmLevel } from "@/lib/therapyTimeline"
import type { MdmChoicesRequest, MdmReview } from "@/types/mdm"
import type { Note } from "@/types/notes"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"
import { useNoteType } from "./useNoteTypes"

/**
 * Under the note's detail key, so an edit to the note refreshes it. The
 * note's `updatedAt` is in the key too: confirming the psychotherapy minutes
 * or redrafting changes the note without touching that key.
 */
const mdmKey = (noteId: string) => [...queryKeys.notes.detail(noteId), "mdm"] as const

export function useMdmReview(noteId: string, updatedAt: string, enabled = true) {
  return useAuthQuery<MdmReview>({
    queryKey: [...mdmKey(noteId), updatedAt],
    queryFn: () => getMdmReview(noteId),
    enabled,
  })
}

/** The note's MDM level once all three elements are chosen; null otherwise, or without a note. */
export function useMdmLevel(note: Note | null | undefined): MdmLevel | null {
  const { data: noteType } = useNoteType(note?.note_type ?? "", note?.note_type_version)
  const offered = !!note && !!noteType && offersMdmReview(noteType.inputs)
  const { data: review } = useMdmReview(note?.id ?? "", note?.updated_at ?? "", offered)
  const level = review?.level
  return MDM_LEVELS.find((l) => l === level) ?? null
}

export function useSaveMdmChoices(noteId: string) {
  return useAuthMutation<MdmReview, MdmChoicesRequest>({
    mutationFn: (data) => putMdmChoices(noteId, data),
    invalidateKeys: [mdmKey(noteId)],
  })
}

/** The codes go into the note's text, so the note it belongs to is read again. */
export function useApplyMdmCodes(noteId: string, sessionId: string | null) {
  return useAuthMutation<MdmReview, void>({
    mutationFn: () => applyMdmCodes(noteId),
    invalidateKeys: [
      queryKeys.notes.detail(noteId),
      ...(sessionId ? [queryKeys.sessions.detail(sessionId)] : []),
    ],
  })
}
