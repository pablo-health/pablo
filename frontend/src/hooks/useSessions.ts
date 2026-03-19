// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Session React Query Hooks
 *
 * Custom hooks for session management using React Query.
 * Includes optimistic updates, cache invalidation, and error handling.
 */

"use client"

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query"
import type {
  FinalizeSessionRequest,
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
import { useConfig } from "@/lib/config"
import { mockSessionListResponse, mockSessionResponses } from "@/lib/mockData"

// ============================================================================
// QUERY HOOKS (Read Operations)
// ============================================================================

/**
 * Fetch list of all sessions
 *
 * Returns sessions across all patients, ordered by session_date (newest first).
 *
 * @param token - Optional auth token for server-side queries
 *
 * @example
 * function SessionList() {
 *   const { data, isLoading } = useSessionList()
 *   if (isLoading) return <div>Loading...</div>
 *   return (
 *     <div>
 *       {data.data.map(session => (
 *         <div key={session.id}>
 *           {session.patient_name} - {session.session_date}
 *         </div>
 *       ))}
 *     </div>
 *   )
 * }
 */
export function useSessionList(token?: string) {
  const { dataMode } = useConfig()
  const isMock = dataMode === "mock"

  return useQuery({
    queryKey: queryKeys.sessions.list(),
    queryFn: () => isMock ? Promise.resolve(mockSessionListResponse) : listSessions(token),
    staleTime: isMock ? Infinity : 60 * 1000,
  })
}

/**
 * Fetch a single session by ID
 *
 * @param sessionId - Session ID
 * @param token - Optional auth token
 * @param options - Query options
 *
 * @example
 * function SessionDetails({ id }: { id: string }) {
 *   const { data: session, isLoading } = useSession(id)
 *   if (isLoading) return <div>Loading...</div>
 *   return (
 *     <div>
 *       <h1>{session.patient_name}</h1>
 *       <p>Status: {session.status}</p>
 *       {session.soap_note && <SOAPNoteDisplay note={session.soap_note} />}
 *     </div>
 *   )
 * }
 */
export function useSession(
  sessionId: string,
  token?: string,
  options?: Omit<UseQueryOptions<SessionResponse>, "queryKey" | "queryFn">
) {
  const { dataMode } = useConfig()
  const isMock = dataMode === "mock"

  return useQuery({
    queryKey: queryKeys.sessions.detail(sessionId),
    queryFn: () => {
      if (isMock) {
        const session = mockSessionResponses.find((s) => s.id === sessionId)
        if (!session) return Promise.reject(new Error(`Session ${sessionId} not found`))
        return Promise.resolve(session)
      }
      return getSession(sessionId, token)
    },
    staleTime: isMock ? Infinity : 60 * 1000,
    ...options,
  })
}

// ============================================================================
// MUTATION HOOKS (Write Operations)
// ============================================================================

/**
 * Upload a session transcript and generate SOAP note
 *
 * This creates a session, generates AI SOAP note, and updates patient metadata.
 * The session will be in "pending_review" status on success.
 *
 * Features:
 * - Automatic cache invalidation for sessions and patient
 * - Error handling for SOAP generation failures
 * - Updates patient session_count and last_session_date
 *
 * @param token - Optional auth token
 *
 * @example
 * function UploadSessionForm({ patientId }: { patientId: string }) {
 *   const uploadMutation = useUploadSession()
 *
 *   const handleUpload = async (transcript: string) => {
 *     try {
 *       const session = await uploadMutation.mutateAsync({
 *         patientId,
 *         data: {
 *           patient_id: patientId,
 *           session_date: new Date().toISOString(),
 *           transcript: { format: "vtt", content: transcript }
 *         }
 *       })
 *       console.log("Session created:", session.id)
 *       console.log("SOAP generated:", session.soap_note)
 *     } catch (error) {
 *       if (error instanceof ApiError && error.code === "SOAP_GENERATION_FAILED") {
 *         console.error("AI generation failed:", error.message)
 *       }
 *     }
 *   }
 *
 *   return <form>...</form>
 * }
 */
export function useUploadSession(token?: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      patientId,
      data,
    }: {
      patientId: string
      data: UploadSessionRequest
    }) => uploadSession(patientId, data, token),

    onSuccess: (session) => {
      // Invalidate session lists
      queryClient.invalidateQueries({ queryKey: queryKeys.sessions.lists() })

      // Invalidate the patient (session_count and last_session_date changed)
      queryClient.invalidateQueries({
        queryKey: queryKeys.patients.detail(session.patient_id),
      })

      // Invalidate patient lists (last_session_date might affect sorting)
      queryClient.invalidateQueries({ queryKey: queryKeys.patients.lists() })
    },
  })
}

/**
 * Finalize a session after review
 *
 * Moves session from "pending_review" to "finalized".
 * Therapist provides quality rating and optionally edits the SOAP note.
 *
 * Features:
 * - Optimistic update to session status
 * - Rollback on error
 *
 * @param token - Optional auth token
 *
 * @example
 * function ReviewSession({ sessionId }: { sessionId: string }) {
 *   const { data: session } = useSession(sessionId)
 *   const finalizeMutation = useFinalizeSession()
 *
 *   const handleAccept = async () => {
 *     await finalizeMutation.mutateAsync({
 *       sessionId,
 *       data: { quality_rating: 5 }
 *     })
 *   }
 *
 *   const handleEdit = async (editedSOAP: SOAPNoteModel) => {
 *     await finalizeMutation.mutateAsync({
 *       sessionId,
 *       data: { quality_rating: 4, soap_note_edited: editedSOAP }
 *     })
 *   }
 *
 *   return <div>...</div>
 * }
 */
export function useFinalizeSession(token?: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      sessionId,
      data,
    }: {
      sessionId: string
      data: FinalizeSessionRequest
    }) => finalizeSession(sessionId, data, token),

    // Optimistic update
    onMutate: async ({ sessionId, data }) => {
      await queryClient.cancelQueries({
        queryKey: queryKeys.sessions.detail(sessionId),
      })

      const previousSession = queryClient.getQueryData<SessionResponse>(
        queryKeys.sessions.detail(sessionId)
      )

      if (previousSession) {
        queryClient.setQueryData<SessionResponse>(
          queryKeys.sessions.detail(sessionId),
          {
            ...previousSession,
            status: "finalized",
            quality_rating: data.quality_rating,
            soap_note_edited: data.soap_note_edited ?? previousSession.soap_note_edited,
            finalized_at: new Date().toISOString(),
          }
        )
      }

      return { previousSession }
    },

    onError: (_error, { sessionId }, context) => {
      if (context?.previousSession) {
        queryClient.setQueryData(
          queryKeys.sessions.detail(sessionId),
          context.previousSession
        )
      }
    },

    onSettled: (_data, _error, { sessionId }) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.sessions.detail(sessionId),
      })
      queryClient.invalidateQueries({ queryKey: queryKeys.sessions.lists() })
    },
  })
}

/**
 * Update quality rating for a finalized session
 *
 * Allows changing the rating after finalization.
 *
 * @param token - Optional auth token
 *
 * @example
 * function SessionRating({ sessionId, currentRating }: Props) {
 *   const updateRatingMutation = useUpdateSessionRating()
 *
 *   const handleRatingChange = async (newRating: number) => {
 *     await updateRatingMutation.mutateAsync({
 *       sessionId,
 *       data: { quality_rating: newRating }
 *     })
 *   }
 *
 *   return <StarRating value={currentRating} onChange={handleRatingChange} />
 * }
 */
export function useUpdateSessionRating(token?: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      sessionId,
      data,
    }: {
      sessionId: string
      data: UpdateSessionRatingRequest
    }) => updateSessionRating(sessionId, data, token),

    // Optimistic update
    onMutate: async ({ sessionId, data }) => {
      await queryClient.cancelQueries({
        queryKey: queryKeys.sessions.detail(sessionId),
      })

      const previousSession = queryClient.getQueryData<SessionResponse>(
        queryKeys.sessions.detail(sessionId)
      )

      if (previousSession) {
        queryClient.setQueryData<SessionResponse>(
          queryKeys.sessions.detail(sessionId),
          { ...previousSession, quality_rating: data.quality_rating }
        )
      }

      return { previousSession }
    },

    onError: (_error, { sessionId }, context) => {
      if (context?.previousSession) {
        queryClient.setQueryData(
          queryKeys.sessions.detail(sessionId),
          context.previousSession
        )
      }
    },

    onSettled: (_data, _error, { sessionId }) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.sessions.detail(sessionId),
      })
      queryClient.invalidateQueries({ queryKey: queryKeys.sessions.lists() })
    },
  })
}
