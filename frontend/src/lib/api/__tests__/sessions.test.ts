// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Session API Function Tests
 *
 * Tests that API functions call the client correctly with proper endpoints and payloads.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import * as client from "../client"
import {
  uploadSession,
  listSessions,
  getSession,
  finalizeSession,
  updateSessionRating,
} from "../sessions"
import { createMockSession } from "@/test/factories"

vi.mock("../client")

describe("Session API Functions", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe("uploadSession", () => {
    it("calls post with correct endpoint and data", async () => {
      const mockSession = createMockSession({
        processing_started_at: "2024-01-15T14:30:01Z",
        processing_completed_at: "2024-01-15T14:30:05Z",
      })

      vi.mocked(client.post).mockResolvedValue(mockSession)

      const uploadData = {
        patient_id: "patient-456",
        session_date: "2024-01-15T14:30:00Z",
        transcript: { format: "vtt" as const, content: "WEBVTT\n\n..." },
      }

      const result = await uploadSession("patient-456", uploadData)

      expect(client.post).toHaveBeenCalledWith(
        "/api/patients/patient-456/sessions/upload",
        uploadData,
        undefined
      )
      expect(result).toEqual(mockSession)
    })

    it("passes token when provided", async () => {
      const mockSession = createMockSession({
        transcript: { format: "vtt", content: "test" },
      })

      vi.mocked(client.post).mockResolvedValue(mockSession)

      const uploadData = {
        patient_id: "patient-456",
        session_date: "2024-01-15T14:30:00Z",
        transcript: { format: "vtt" as const, content: "test" },
      }

      await uploadSession("patient-456", uploadData, "test-token")

      expect(client.post).toHaveBeenCalledWith(
        "/api/patients/patient-456/sessions/upload",
        uploadData,
        "test-token"
      )
    })

    it("handles different transcript formats", async () => {
      const mockSession = createMockSession({
        transcript: { format: "json", content: '{"text": "..."}' },
      })

      vi.mocked(client.post).mockResolvedValue(mockSession)

      const uploadData = {
        patient_id: "patient-456",
        session_date: "2024-01-15T14:30:00Z",
        transcript: { format: "json" as const, content: '{"text": "..."}' },
      }

      await uploadSession("patient-456", uploadData)

      expect(client.post).toHaveBeenCalledWith(
        "/api/patients/patient-456/sessions/upload",
        uploadData,
        undefined
      )
    })
  })

  describe("listSessions", () => {
    it("calls get with correct endpoint", async () => {
      const mockResponse = {
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      }

      vi.mocked(client.get).mockResolvedValue(mockResponse)

      const result = await listSessions()

      expect(client.get).toHaveBeenCalledWith("/api/sessions", undefined)
      expect(result).toEqual(mockResponse)
    })

    it("passes token when provided", async () => {
      const mockResponse = {
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      }

      vi.mocked(client.get).mockResolvedValue(mockResponse)

      await listSessions("test-token")

      expect(client.get).toHaveBeenCalledWith("/api/sessions", "test-token")
    })
  })

  describe("getSession", () => {
    it("calls get with correct endpoint", async () => {
      const mockSession = createMockSession({
        status: "finalized",
        transcript: { format: "vtt", content: "test" },
        processing_started_at: "2024-01-15T14:30:01Z",
        processing_completed_at: "2024-01-15T14:30:05Z",
      })

      vi.mocked(client.get).mockResolvedValue(mockSession)

      const result = await getSession("session-123")

      expect(client.get).toHaveBeenCalledWith(
        "/api/sessions/session-123",
        undefined
      )
      expect(result).toEqual(mockSession)
    })

    it("passes token when provided", async () => {
      const mockSession = createMockSession({
        transcript: { format: "vtt", content: "test" },
      })

      vi.mocked(client.get).mockResolvedValue(mockSession)

      await getSession("session-123", "test-token")

      expect(client.get).toHaveBeenCalledWith(
        "/api/sessions/session-123",
        "test-token"
      )
    })
  })

  describe("finalizeSession", () => {
    it("calls patch with correct endpoint for accept workflow", async () => {
      const mockSession = createMockSession({
        status: "finalized",
        transcript: { format: "vtt", content: "test" },
        processing_started_at: "2024-01-15T14:30:01Z",
        processing_completed_at: "2024-01-15T14:30:05Z",
      })

      vi.mocked(client.patch).mockResolvedValue(mockSession)

      const finalizeData = { quality_rating: 5 }
      const result = await finalizeSession("session-123", finalizeData)

      expect(client.patch).toHaveBeenCalledWith(
        "/api/sessions/session-123/finalize",
        finalizeData,
        undefined
      )
      expect(result).toEqual(mockSession)
    })

    it("calls patch with edited SOAP note", async () => {
      const mockSession = createMockSession({
        status: "finalized",
        transcript: { format: "vtt", content: "test" },
        processing_started_at: "2024-01-15T14:30:01Z",
        processing_completed_at: "2024-01-15T14:30:05Z",
      })

      vi.mocked(client.patch).mockResolvedValue(mockSession)

      const finalizeData = {
        quality_rating: 4,
        soap_note_edited: {
          subjective: "edited",
          objective: "edited",
          assessment: "edited",
          plan: "edited",
        },
      }

      await finalizeSession("session-123", finalizeData)

      expect(client.patch).toHaveBeenCalledWith(
        "/api/sessions/session-123/finalize",
        finalizeData,
        undefined
      )
    })

    it("passes token when provided", async () => {
      const mockSession = createMockSession({
        status: "finalized",
        transcript: { format: "vtt", content: "test" },
      })

      vi.mocked(client.patch).mockResolvedValue(mockSession)

      await finalizeSession(
        "session-123",
        { quality_rating: 5 },
        "test-token"
      )

      expect(client.patch).toHaveBeenCalledWith(
        "/api/sessions/session-123/finalize",
        { quality_rating: 5 },
        "test-token"
      )
    })
  })

  describe("updateSessionRating", () => {
    it("calls patch with correct endpoint", async () => {
      const mockSession = createMockSession({
        status: "finalized",
        transcript: { format: "vtt", content: "test" },
      })

      vi.mocked(client.patch).mockResolvedValue(mockSession)

      const ratingData = { quality_rating: 5 }
      const result = await updateSessionRating("session-123", ratingData)

      expect(client.patch).toHaveBeenCalledWith(
        "/api/sessions/session-123/rating",
        ratingData,
        undefined
      )
      expect(result).toEqual(mockSession)
    })

    it("passes token when provided", async () => {
      const mockSession = createMockSession({
        status: "finalized",
        transcript: { format: "vtt", content: "test" },
      })

      vi.mocked(client.patch).mockResolvedValue(mockSession)

      await updateSessionRating(
        "session-123",
        { quality_rating: 4 },
        "test-token"
      )

      expect(client.patch).toHaveBeenCalledWith(
        "/api/sessions/session-123/rating",
        { quality_rating: 4 },
        "test-token"
      )
    })
  })
})
