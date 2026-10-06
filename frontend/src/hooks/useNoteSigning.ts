// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useQuery } from "@tanstack/react-query"
import type {
  CreateNoteAddendumRequest,
  Note,
  NoteAddendum,
  NoteSigningRecord,
  SignNoteRequest,
  UnlockNoteRequest,
} from "@/types/notes"
import {
  addNoteAddendum,
  fetchNoteSigning,
  signNote,
  unlockNote,
} from "@/lib/api/notes"
import { queryKeys } from "@/lib/api/queryKeys"
import { getUserStatus } from "@/lib/api/users"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** The note's signature, signed versions and addenda. */
export function useNoteSigning(noteId: string | undefined, token?: string) {
  return useAuthQuery<NoteSigningRecord>({
    queryKey: queryKeys.notes.signing(noteId ?? ""),
    queryFn: () => fetchNoteSigning(noteId!, token),
    enabled: !!noteId,
  })
}

/**
 * Everything that shows a note's lock state: the note itself, its signing
 * record, the patient's note list, and the session pages that embed the note.
 */
function signingKeys(noteId: string, patientId?: string) {
  return [
    queryKeys.notes.detail(noteId),
    queryKeys.notes.signing(noteId),
    ...(patientId ? [queryKeys.notes.byPatient(patientId)] : []),
    queryKeys.sessions.details(),
    queryKeys.sessions.lists(),
  ]
}

export function useSignNote(token?: string) {
  return useAuthMutation<Note, { noteId: string; data: SignNoteRequest }>({
    mutationFn: ({ noteId, data }) => signNote(noteId, data, token),
    invalidateKeys: ({ noteId }, note) => signingKeys(noteId, note?.patient_id),
  })
}

export function useUnlockNote(token?: string) {
  return useAuthMutation<Note, { noteId: string; data: UnlockNoteRequest }>({
    mutationFn: ({ noteId, data }) => unlockNote(noteId, data, token),
    invalidateKeys: ({ noteId }, note) => signingKeys(noteId, note?.patient_id),
  })
}

export function useAddNoteAddendum(token?: string) {
  return useAuthMutation<
    NoteAddendum,
    { noteId: string; data: CreateNoteAddendumRequest }
  >({
    mutationFn: ({ noteId, data }) => addNoteAddendum(noteId, data, token),
    invalidateKeys: ({ noteId, data }) => [
      queryKeys.notes.signing(noteId),
      // A signed draft from a dictation stops being offered.
      ...(data.dictation_id ? [queryKeys.sessions.allDictations()] : []),
    ],
  })
}

/** The name and credentials a signature starts from: the signer's own profile. */
export function useSignerDefaults(): { name: string; credentials: string } {
  const { data } = useQuery({
    queryKey: ["user", "status"],
    queryFn: () => getUserStatus(),
    staleTime: 5 * 60 * 1000,
  })
  return { name: data?.name ?? "", credentials: data?.credentials ?? "" }
}
