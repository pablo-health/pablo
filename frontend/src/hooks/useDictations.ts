// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { addSessionDictation, listSessionDictations } from "@/lib/api/dictations"
import { queryKeys } from "@/lib/api/queryKeys"
import type { SessionDictation, SessionDictationList } from "@/types/dictations"
import type { RedraftEdits } from "@/types/notes"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** What was dictated for the session; polled while a clip is transcribing. */
export function useSessionDictations(sessionId: string | undefined, token?: string) {
  return useAuthQuery<SessionDictationList>({
    queryKey: queryKeys.sessions.dictations(sessionId ?? ""),
    queryFn: () => listSessionDictations(sessionId!, token),
    enabled: !!sessionId,
    refetchInterval: (query) =>
      query.state.data?.data.some((d) => d.status === "transcribing") ? 3000 : false,
  })
}

export interface AddDictationVariables {
  sessionId: string
  audio: Blob
  durationSeconds?: number
  edits?: RedraftEdits
}

export function useAddSessionDictation(token?: string) {
  return useAuthMutation<SessionDictation, AddDictationVariables>({
    mutationFn: ({ sessionId, audio, durationSeconds, edits }) =>
      addSessionDictation(sessionId, audio, { durationSeconds, edits }, token),
    // The note may now be redrafting, which the session shows.
    invalidateKeys: ({ sessionId }) => [
      queryKeys.sessions.dictations(sessionId),
      queryKeys.sessions.detail(sessionId),
    ],
  })
}
