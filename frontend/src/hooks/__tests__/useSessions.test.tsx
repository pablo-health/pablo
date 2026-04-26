// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Session React Query Hook Tests
 *
 * Tests hooks with real QueryClient, mock API functions.
 * Includes optimistic update and cache invalidation tests.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  useSessionList,
  useSession,
  useUploadSession,
  useFinalizeSession,
  useUpdateSessionRating,
} from "../useSessions"
import * as sessionsApi from "@/lib/api/sessions"
import type { SessionResponse } from "@/types/sessions"
import { createMockNote, createMockSession } from "@/test/factories"

vi.mock("@/lib/api/sessions")
vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "QueryWrapper"
  return Wrapper
}

const mockSession: SessionResponse = createMockSession({
  note: createMockNote({
    content: {
      subjective: "Patient reports improved mood",
      objective: "Patient appeared calm",
      assessment: "Continued progress",
      plan: "Continue weekly sessions",
    },
  }),
  processing_started_at: "2024-01-15T14:30:01Z",
  processing_completed_at: "2024-01-15T14:30:05Z",
})

describe("useSessions hooks", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe("useSessionList", () => {
    it("fetches session list successfully", async () => {
      const mockData = {
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      }

      vi.mocked(sessionsApi.listSessions).mockResolvedValue(mockData)

      const { result } = renderHook(() => useSessionList(), {
        wrapper: createWrapper(),
      })

      expect(result.current.isLoading).toBe(true)

      await waitFor(() => expect(result.current.isSuccess).toBe(true))

      expect(result.current.data).toEqual(mockData)
      expect(sessionsApi.listSessions).toHaveBeenCalledWith(undefined)
    })

    it("passes token when provided", async () => {
      const mockData = {
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      }

      vi.mocked(sessionsApi.listSessions).mockResolvedValue(mockData)

      renderHook(() => useSessionList("test-token"), {
        wrapper: createWrapper(),
      })

      await waitFor(() =>
        expect(sessionsApi.listSessions).toHaveBeenCalledWith("test-token")
      )
    })

    it("handles API errors", async () => {
      const error = new Error("API Error")
      vi.mocked(sessionsApi.listSessions).mockRejectedValue(error)

      const { result } = renderHook(() => useSessionList(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => expect(result.current.isError).toBe(true))

      expect(result.current.error).toEqual(error)
    })
  })

  describe("useSession", () => {
    it("fetches single session successfully", async () => {
      vi.mocked(sessionsApi.getSession).mockResolvedValue(mockSession)

      const { result } = renderHook(() => useSession("session-123"), {
        wrapper: createWrapper(),
      })

      expect(result.current.isLoading).toBe(true)

      await waitFor(() => expect(result.current.isSuccess).toBe(true))

      expect(result.current.data).toEqual(mockSession)
      expect(sessionsApi.getSession).toHaveBeenCalledWith(
        "session-123",
        undefined
      )
    })

    it("respects enabled option", async () => {
      vi.mocked(sessionsApi.getSession).mockResolvedValue(mockSession)

      const { result } = renderHook(
        () => useSession("session-123", undefined, { enabled: false }),
        {
          wrapper: createWrapper(),
        }
      )

      expect(result.current.isLoading).toBe(false)
      expect(sessionsApi.getSession).not.toHaveBeenCalled()
    })

    it("passes token when provided", async () => {
      vi.mocked(sessionsApi.getSession).mockResolvedValue(mockSession)

      renderHook(() => useSession("session-123", "test-token"), {
        wrapper: createWrapper(),
      })

      await waitFor(() =>
        expect(sessionsApi.getSession).toHaveBeenCalledWith(
          "session-123",
          "test-token"
        )
      )
    })
  })

  describe("useUploadSession", () => {
    it("uploads session and invalidates queries", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      vi.mocked(sessionsApi.uploadSession).mockResolvedValue(mockSession)

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useUploadSession(), { wrapper })

      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

      await result.current.mutateAsync({
        patientId: "patient-456",
        data: {
          patient_id: "patient-456",
          session_date: "2024-01-15T14:30:00Z",
          transcript: { format: "vtt", content: "test" },
        },
      })

      // Should invalidate session lists
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["sessions", "list"],
      })

      // Should invalidate patient (session_count changed)
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["patients", "detail", "patient-456"],
      })

      // Should invalidate patient lists (last_session_date changed)
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["patients", "list"],
      })
    })

    it("returns uploaded session data", async () => {
      vi.mocked(sessionsApi.uploadSession).mockResolvedValue(mockSession)

      const { result } = renderHook(() => useUploadSession(), {
        wrapper: createWrapper(),
      })

      const session = await result.current.mutateAsync({
        patientId: "patient-456",
        data: {
          patient_id: "patient-456",
          session_date: "2024-01-15T14:30:00Z",
          transcript: { format: "vtt", content: "test" },
        },
      })

      expect(session).toEqual(mockSession)
    })

    it("handles upload errors", async () => {
      const error = new Error("Upload failed")
      vi.mocked(sessionsApi.uploadSession).mockRejectedValue(error)

      const { result } = renderHook(() => useUploadSession(), {
        wrapper: createWrapper(),
      })

      await expect(
        result.current.mutateAsync({
          patientId: "patient-456",
          data: {
            patient_id: "patient-456",
            session_date: "2024-01-15T14:30:00Z",
            transcript: { format: "vtt", content: "test" },
          },
        })
      ).rejects.toThrow("Upload failed")
    })
  })

  describe("useFinalizeSession", () => {
    it("finalizes session with optimistic status update", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      // Pre-populate cache with pending_review session
      queryClient.setQueryData(["sessions", "detail", "session-123"], mockSession)

      const finalizedSession: SessionResponse = {
        ...mockSession,
        status: "finalized",
        note: {
          ...mockSession.note!,
          quality_rating: 5,
          finalized_at: "2024-01-15T14:35:00Z",
        },
      }

      vi.mocked(sessionsApi.finalizeSession).mockResolvedValue(finalizedSession)

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useFinalizeSession(), { wrapper })

      result.current.mutate({
        sessionId: "session-123",
        data: { quality_rating: 5 },
      })

      // Check optimistic update happened immediately
      await waitFor(() => {
        const cachedData = queryClient.getQueryData<SessionResponse>([
          "sessions",
          "detail",
          "session-123",
        ])
        expect(cachedData?.status).toBe("finalized")
        expect(cachedData?.note?.quality_rating).toBe(5)
      })
    })

    it("includes edited SOAP note in optimistic update", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      queryClient.setQueryData(["sessions", "detail", "session-123"], mockSession)

      const editedSOAP = {
        subjective: "edited",
        objective: "edited",
        assessment: "edited",
        plan: "edited",
      }

      const finalizedSession: SessionResponse = {
        ...mockSession,
        status: "finalized",
        note: {
          ...mockSession.note!,
          quality_rating: 4,
          content_edited: editedSOAP,
          finalized_at: "2024-01-15T14:35:00Z",
        },
      }

      vi.mocked(sessionsApi.finalizeSession).mockResolvedValue(finalizedSession)

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useFinalizeSession(), { wrapper })

      result.current.mutate({
        sessionId: "session-123",
        data: { quality_rating: 4, soap_note_edited: editedSOAP },
      })

      await waitFor(() => {
        const cachedData = queryClient.getQueryData<SessionResponse>([
          "sessions",
          "detail",
          "session-123",
        ])
        expect(cachedData?.note?.content_edited).toEqual(editedSOAP)
      })
    })

    it("rolls back optimistic update on error", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      // Pre-populate cache with original session
      queryClient.setQueryData(["sessions", "detail", "session-123"], mockSession)

      const error = new Error("Finalize failed")
      vi.mocked(sessionsApi.finalizeSession).mockRejectedValue(error)

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useFinalizeSession(), { wrapper })

      try {
        await result.current.mutateAsync({
          sessionId: "session-123",
          data: { quality_rating: 5 },
        })
      } catch {
        // Expected error
      }

      await waitFor(() => {
        // Should roll back to original data
        const cachedData = queryClient.getQueryData<SessionResponse>([
          "sessions",
          "detail",
          "session-123",
        ])
        expect(cachedData).toEqual(mockSession)
      })
    })

    it("invalidates session queries on success", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      queryClient.setQueryData(["sessions", "detail", "session-123"], mockSession)

      const finalizedSession: SessionResponse = {
        ...mockSession,
        status: "finalized",
        note: { ...mockSession.note!, quality_rating: 5 },
      }

      vi.mocked(sessionsApi.finalizeSession).mockResolvedValue(finalizedSession)

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useFinalizeSession(), { wrapper })

      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

      await result.current.mutateAsync({
        sessionId: "session-123",
        data: { quality_rating: 5 },
      })

      // Should invalidate session detail
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["sessions", "detail", "session-123"],
      })

      // Should invalidate session lists
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["sessions", "list"],
      })
    })
  })

  describe("useUpdateSessionRating", () => {
    it("updates rating with optimistic update", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      const finalizedSession: SessionResponse = {
        ...mockSession,
        status: "finalized",
        note: { ...mockSession.note!, quality_rating: 4 },
      }

      queryClient.setQueryData(
        ["sessions", "detail", "session-123"],
        finalizedSession
      )

      const updatedSession: SessionResponse = {
        ...finalizedSession,
        note: { ...finalizedSession.note!, quality_rating: 5 },
      }

      vi.mocked(sessionsApi.updateSessionRating).mockResolvedValue(
        updatedSession
      )

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useUpdateSessionRating(), { wrapper })

      result.current.mutate({
        sessionId: "session-123",
        data: { quality_rating: 5 },
      })

      // Check optimistic update
      await waitFor(() => {
        const cachedData = queryClient.getQueryData<SessionResponse>([
          "sessions",
          "detail",
          "session-123",
        ])
        expect(cachedData?.note?.quality_rating).toBe(5)
      })
    })

    it("rolls back on error", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      const finalizedSession: SessionResponse = {
        ...mockSession,
        status: "finalized",
        note: { ...mockSession.note!, quality_rating: 4 },
      }

      queryClient.setQueryData(
        ["sessions", "detail", "session-123"],
        finalizedSession
      )

      const error = new Error("Update failed")
      vi.mocked(sessionsApi.updateSessionRating).mockRejectedValue(error)

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useUpdateSessionRating(), { wrapper })

      try {
        await result.current.mutateAsync({
          sessionId: "session-123",
          data: { quality_rating: 5 },
        })
      } catch {
        // Expected error
      }

      await waitFor(() => {
        const cachedData = queryClient.getQueryData<SessionResponse>([
          "sessions",
          "detail",
          "session-123",
        ])
        expect(cachedData?.note?.quality_rating).toBe(4)
      })
    })

    it("invalidates session queries on success", async () => {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })

      const finalizedSession: SessionResponse = {
        ...mockSession,
        status: "finalized",
        note: { ...mockSession.note!, quality_rating: 4 },
      }

      queryClient.setQueryData(
        ["sessions", "detail", "session-123"],
        finalizedSession
      )

      const updatedSession: SessionResponse = {
        ...finalizedSession,
        note: { ...finalizedSession.note!, quality_rating: 5 },
      }

      vi.mocked(sessionsApi.updateSessionRating).mockResolvedValue(
        updatedSession
      )

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      const { result } = renderHook(() => useUpdateSessionRating(), { wrapper })

      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

      await result.current.mutateAsync({
        sessionId: "session-123",
        data: { quality_rating: 5 },
      })

      // Should invalidate session detail
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["sessions", "detail", "session-123"],
      })

      // Should invalidate session lists
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["sessions", "list"],
      })
    })
  })
})
