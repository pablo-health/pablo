// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  CreateStandaloneNoteRequest,
  FinalizeNoteRequest,
  Note,
  PatientNotesListResponse,
  UpdateNoteEditsRequest,
} from "@/types/notes"
import {
  createStandaloneNote,
  fetchNote,
  finalizeNote,
  listNotesForPatient,
  updateNoteEdits,
} from "@/lib/api/notes"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export function useNote(noteId: string | undefined, token?: string) {
  return useAuthQuery<Note>({
    queryKey: queryKeys.notes.detail(noteId ?? ""),
    queryFn: () => fetchNote(noteId!, token),
    enabled: !!noteId,
  })
}

export function usePatientNotes(patientId: string | undefined, token?: string) {
  return useAuthQuery<PatientNotesListResponse>({
    queryKey: queryKeys.notes.byPatient(patientId ?? ""),
    queryFn: () => listNotesForPatient(patientId!, token),
    enabled: !!patientId,
  })
}

export function useUpdateNoteEdits(token?: string) {
  return useAuthMutation<
    Note,
    { noteId: string; data: UpdateNoteEditsRequest },
    Note
  >({
    mutationFn: ({ noteId, data }) => updateNoteEdits(noteId, data, token),
    invalidateKeys: ({ noteId }, data) => [
      queryKeys.notes.detail(noteId),
      ...(data ? [queryKeys.notes.byPatient(data.patient_id)] : []),
    ],
    optimistic: {
      queryKey: ({ noteId }) => queryKeys.notes.detail(noteId),
      updater: (previous, { data }) => ({
        ...previous,
        content_edited: data.content_edited,
      }),
    },
  })
}

export function useFinalizeNote(token?: string) {
  return useAuthMutation<
    Note,
    { noteId: string; data: FinalizeNoteRequest },
    Note
  >({
    mutationFn: ({ noteId, data }) => finalizeNote(noteId, data, token),
    invalidateKeys: ({ noteId }, data) => [
      queryKeys.notes.detail(noteId),
      ...(data ? [queryKeys.notes.byPatient(data.patient_id)] : []),
      queryKeys.sessions.lists(),
    ],
    optimistic: {
      queryKey: ({ noteId }) => queryKeys.notes.detail(noteId),
      updater: (previous, { data }) => ({
        ...previous,
        finalized_at: new Date().toISOString(),
        quality_rating: data.quality_rating,
        quality_rating_reason: data.quality_rating_reason ?? null,
        quality_rating_sections: data.quality_rating_sections ?? null,
      }),
    },
  })
}

export function useCreateStandaloneNote(token?: string) {
  return useAuthMutation<
    Note,
    { patientId: string; data: CreateStandaloneNoteRequest }
  >({
    mutationFn: ({ patientId, data }) =>
      createStandaloneNote(patientId, data, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.notes.byPatient(patientId),
      queryKeys.patients.detail(patientId),
    ],
  })
}
