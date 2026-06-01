// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState, useEffect } from "react"
import { useQuery, type UseQueryOptions } from "@tanstack/react-query"
import type {
  FinalizeSessionRequest,
  SessionListResponse,
  SessionResponse,
  UpdateSessionMetadataRequest,
  UpdateSessionRatingRequest,
  UploadSessionRequest,
} from "@/types/sessions"
import {
  finalizeSession,
  getSession,
  listSessions,
  updateSessionMetadata,
  updateSessionRating,
  uploadSession,
} from "@/lib/api/sessions"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuth } from "@/lib/auth-context"
import { useConfig } from "@/lib/config"
import { mockSessionListResponse, mockSessionResponses } from "@/lib/mockData"
import { useAuthMutation } from "./useAuthQuery"

// Query hooks — mock-aware, so they use raw useQuery instead of useAuthQuery.

export function useSessionList(
  token?: string,
  options?: Omit<UseQueryOptions<SessionListResponse>, "queryKey" | "queryFn">,
) {
  const { loading } = useAuth()
  const { dataMode } = useConfig()
  const isMock = dataMode === "mock"

  return useQuery({
    queryKey: queryKeys.sessions.list(),
    queryFn: () =>
      isMock ? Promise.resolve(mockSessionListResponse) : listSessions(token),
    staleTime: isMock ? Infinity : 60 * 1000,
    enabled: (options?.enabled ?? true) && (isMock || !loading),
    ...options,
  })
}

const SESSION_DISCOVERY_TIMEOUT_MS = 60_000

/**
 * Polls the session list after a transcript upload until the newly-created
 * in-flight session (queued/processing) for `patientId` becomes visible, then
 * returns its id so the caller can poll the session detail.
 *
 * Keeps polling until the session is discovered — the row may not be committed
 * for ~1s after the upload POST starts, and the list cache is usually warm —
 * then locks the id and stops. If nothing appears within
 * SESSION_DISCOVERY_TIMEOUT_MS the upload almost certainly failed, so it reports
 * `timedOut` and the caller surfaces an error instead of spinning forever.
 */
export function useSessionProcessing(patientId: string | null): {
  sessionId: string | null
  timedOut: boolean
} {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [timedOut, setTimedOut] = useState(false)

  // Reset state whenever the tracked patient changes (overlay open/close/re-upload).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSessionId(null)
    setTimedOut(false)
  }, [patientId])

  const searching = patientId !== null && sessionId === null && !timedOut

  const { data: list } = useSessionList(undefined, {
    enabled: searching,
    staleTime: 0,
    refetchInterval: searching ? 2000 : false,
  })

  // Respond to each new list snapshot: lock in the session id when it appears.
  useEffect(() => {
    if (!searching) return
    const inFlight = list?.data.find(
      (s) =>
        s.patient_id === patientId &&
        (s.status === "queued" || s.status === "processing"),
    )
    if (inFlight) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSessionId(inFlight.id)
    }
  }, [list, searching, patientId])

  // Backstop: if no session is found within the discovery window, mark as
  // timed out so the caller can surface an error instead of spinning forever.
  useEffect(() => {
    if (!searching) return
    const timerId = setTimeout(() => {
      setTimedOut(true)
    }, SESSION_DISCOVERY_TIMEOUT_MS)
    return () => clearTimeout(timerId)
  }, [searching])

  return { sessionId, timedOut }
}

export function useSession(
  sessionId: string,
  token?: string,
  options?: Omit<UseQueryOptions<SessionResponse>, "queryKey" | "queryFn">,
) {
  const { dataMode } = useConfig()
  const isMock = dataMode === "mock"

  return useQuery({
    queryKey: queryKeys.sessions.detail(sessionId),
    queryFn: () => {
      if (isMock) {
        const session = mockSessionResponses.find((s) => s.id === sessionId)
        if (!session)
          return Promise.reject(new Error(`Session ${sessionId} not found`))
        return Promise.resolve(session)
      }
      return getSession(sessionId, token)
    },
    staleTime: isMock ? Infinity : 60 * 1000,
    ...options,
  })
}

// Mutation hooks

export function useUploadSession(token?: string) {
  return useAuthMutation<
    SessionResponse,
    { patientId: string; data: UploadSessionRequest }
  >({
    mutationFn: ({ patientId, data }) => uploadSession(patientId, data, token),
    invalidateKeys: (_vars, data) =>
      data
        ? [
            queryKeys.sessions.lists(),
            queryKeys.patients.detail(data.patient_id),
            queryKeys.patients.lists(),
          ]
        : [],
  })
}

export function useFinalizeSession(token?: string) {
  return useAuthMutation<
    SessionResponse,
    { sessionId: string; data: FinalizeSessionRequest },
    SessionResponse
  >({
    mutationFn: ({ sessionId, data }) =>
      finalizeSession(sessionId, data, token),
    invalidateKeys: ({ sessionId }) => [
      queryKeys.sessions.detail(sessionId),
      queryKeys.sessions.lists(),
    ],
    optimistic: {
      queryKey: ({ sessionId }) => queryKeys.sessions.detail(sessionId),
      updater: (previous, { data }) => ({
        ...previous,
        status: "finalized",
        note: previous.note
          ? {
              ...previous.note,
              quality_rating: data.quality_rating ?? previous.note.quality_rating,
              quality_rating_reason:
                data.quality_rating_reason ?? previous.note.quality_rating_reason,
              quality_rating_sections:
                data.quality_rating_sections ?? previous.note.quality_rating_sections,
              content_edited: data.soap_note_edited
                ? { ...data.soap_note_edited }
                : previous.note.content_edited,
              finalized_at: new Date().toISOString(),
            }
          : previous.note,
      }),
    },
  })
}

export function useUpdateSessionRating(token?: string) {
  return useAuthMutation<
    SessionResponse,
    { sessionId: string; data: UpdateSessionRatingRequest },
    SessionResponse
  >({
    mutationFn: ({ sessionId, data }) =>
      updateSessionRating(sessionId, data, token),
    invalidateKeys: ({ sessionId }) => [
      queryKeys.sessions.detail(sessionId),
      queryKeys.sessions.lists(),
    ],
    optimistic: {
      queryKey: ({ sessionId }) => queryKeys.sessions.detail(sessionId),
      updater: (previous, { data }) => ({
        ...previous,
        note: previous.note
          ? { ...previous.note, quality_rating: data.quality_rating }
          : previous.note,
      }),
    },
  })
}

export function useUpdateSessionMetadata(token?: string) {
  return useAuthMutation<
    SessionResponse,
    { sessionId: string; data: UpdateSessionMetadataRequest },
    SessionResponse
  >({
    mutationFn: ({ sessionId, data }) =>
      updateSessionMetadata(sessionId, data, token),
    invalidateKeys: ({ sessionId }) => [
      queryKeys.sessions.detail(sessionId),
      queryKeys.sessions.lists(),
    ],
    optimistic: {
      queryKey: ({ sessionId }) => queryKeys.sessions.detail(sessionId),
      updater: (previous, { data }) =>
        data.session_date
          ? { ...previous, session_date: data.session_date }
          : previous,
    },
  })
}
