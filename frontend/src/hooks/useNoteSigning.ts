// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import type {
  CreateNoteAddendumRequest,
  Note,
  NoteAddendum,
  NoteSignerFields,
  NoteSigningRecord,
  SignNoteRequest,
  UnlockNoteRequest,
} from "@/types/notes"
import { addNoteAddendum, fetchNoteSigning, signNote, unlockNote } from "@/lib/api/notes"
import { queryKeys } from "@/lib/api/queryKeys"
import {
  getUserStatus,
  updateUserProfile,
  type UpdateUserRequest,
  type UserStatus,
} from "@/lib/api/users"
import { withDashboard } from "./dashboardFreshness"
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
 * record, the patient's note list, the session pages that embed the note, and
 * the dashboard's count of notes awaiting signature.
 */
function signingKeys(noteId: string, patientId?: string) {
  return withDashboard([
    queryKeys.notes.detail(noteId),
    queryKeys.notes.signing(noteId),
    ...(patientId ? [queryKeys.notes.byPatient(patientId)] : []),
    queryKeys.sessions.details(),
    queryKeys.sessions.lists(),
  ])
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
  return useAuthMutation<NoteAddendum, { noteId: string; data: CreateNoteAddendumRequest }>({
    mutationFn: ({ noteId, data }) => addNoteAddendum(noteId, data, token),
    invalidateKeys: ({ noteId, data }) => [
      queryKeys.notes.signing(noteId),
      // A signed draft from a dictation stops being offered.
      ...(data.dictation_id ? [queryKeys.sessions.allDictations()] : []),
    ],
  })
}

const USER_STATUS_KEY = ["user", "status"] as const

/**
 * The name and credentials a signature starts from: the signer's own profile.
 *
 * An account made with an email and password has no name of its own, and the
 * server reports its email address as the name. That is never a signature, so
 * the name starts empty instead and the clinician types it once.
 *
 * `loaded` says the profile has been asked for and answered (or refused). A
 * dialog that showed the fields before then showed them empty and filled them
 * in a moment later, under the clinician's hands.
 */
export function useSignerDefaults(): {
  name: string
  credentials: string
  loaded: boolean
} {
  const { data, isLoading } = useQuery({
    queryKey: USER_STATUS_KEY,
    queryFn: () => getUserStatus(),
    staleTime: 5 * 60 * 1000,
  })
  // `isLoading`, not `isPending`: a query that is not enabled stays pending
  // for ever and has nothing to wait for.
  const name = data?.name ?? ""
  return {
    name: isEmailStandIn(name, data?.email) ? "" : name,
    credentials: data?.credentials ?? "",
    loaded: !isLoading,
  }
}

function isEmailStandIn(name: string, email: string | undefined): boolean {
  return !!email && name.trim().toLowerCase() === email.trim().toLowerCase()
}

/**
 * Keep what a clinician signed with, so the next signature starts from it.
 *
 * Called only after a signature has been stored. It writes to the clinician's
 * own profile just the fields that differ from what the dialog started with,
 * and it never fails the signature: if the profile write is refused, the next
 * dialog starts from the profile as it was.
 */
export function useRememberSigner(): (
  signer: NoteSignerFields,
  startedFrom: { name: string; credentials: string },
) => Promise<void> {
  const queryClient = useQueryClient()
  return useCallback(
    async (signer, startedFrom) => {
      const credentials = signer.signer_credentials ?? ""
      const update: UpdateUserRequest = {}
      if (signer.signer_name !== startedFrom.name.trim()) update.name = signer.signer_name
      if (credentials !== startedFrom.credentials.trim()) update.credentials = credentials
      if (Object.keys(update).length === 0) return
      // The next dialog may open before the write answers; let it start from
      // these values already.
      queryClient.setQueryData<UserStatus>(USER_STATUS_KEY, (prev) =>
        prev ? { ...prev, ...update } : prev,
      )
      try {
        await updateUserProfile(update)
      } catch {
        // The signature is stored either way; the re-read below puts the
        // profile's own values back.
      } finally {
        void queryClient.invalidateQueries({ queryKey: USER_STATUS_KEY })
      }
    },
    [queryClient],
  )
}
