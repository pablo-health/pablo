// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useQuery, type UseQueryOptions } from "@tanstack/react-query"
import type {
  FinalizeSessionRequest,
  SessionListResponse,
  SessionResponse,
  UpdateSessionRatingRequest,
  UploadSessionRequest,
} from "@/types/sessions"
import {
  finalizeSession,
  getSession,
  listSessions,
  updateSessionRating,
  uploadSession,
} from "@/lib/api/sessions"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuth } from "@/lib/auth-context"
import { useConfig } from "@/lib/config"
import { useAuthMutation } from "./useAuthQuery"

// Mock data lives in @/lib/mockData and is loaded via dynamic import only in
// dev builds. The IS_DEV_BUILD guard collapses to `false` in production, so
// the prod bundle drops both the import branch and the fixture module.
const IS_DEV_BUILD = process.env.NODE_ENV !== "production"

async function loadSessionListMock(): Promise<SessionListResponse> {
  const { mockSessionListResponse } = await import("@/lib/mockData")
  return mockSessionListResponse
}

async function loadSessionMock(sessionId: string): Promise<SessionResponse> {
  const { mockSessionResponses } = await import("@/lib/mockData")
  const session = mockSessionResponses.find((s) => s.id === sessionId)
  if (!session) throw new Error(`Session ${sessionId} not found`)
  return session
}

// Query hooks — mock-aware, so they use raw useQuery instead of useAuthQuery.

export function useSessionList(token?: string) {
  const { loading } = useAuth()
  const { dataMode } = useConfig()
  const isMock = IS_DEV_BUILD && dataMode === "mock"

  return useQuery({
    queryKey: queryKeys.sessions.list(),
    queryFn: () => (isMock ? loadSessionListMock() : listSessions(token)),
    staleTime: isMock ? Infinity : 60 * 1000,
    enabled: isMock || !loading,
  })
}

export function useSession(
  sessionId: string,
  token?: string,
  options?: Omit<UseQueryOptions<SessionResponse>, "queryKey" | "queryFn">,
) {
  const { dataMode } = useConfig()
  const isMock = IS_DEV_BUILD && dataMode === "mock"

  return useQuery({
    queryKey: queryKeys.sessions.detail(sessionId),
    queryFn: () => (isMock ? loadSessionMock(sessionId) : getSession(sessionId, token)),
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
              quality_rating: data.quality_rating,
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
