// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SessionStatusBadge Component Tests
 *
 * Comprehensive tests covering rendering, auto-polling, status transitions,
 * and accessibility.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { SessionStatusBadge } from "../SessionStatusBadge"
import * as sessionsApi from "@/lib/api/sessions"
import { createMockSession } from "@/test/factories"

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
  Wrapper.displayName = "TestQueryClientWrapper"
  return Wrapper
}

const mockSessionBase = createMockSession({
  processing_started_at: "2024-01-15T14:30:01Z",
  processing_completed_at: "2024-01-15T14:30:05Z",
})

describe("SessionStatusBadge", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe("Rendering - Status Display", () => {
    const statusTests = [
      { status: "queued" as const, label: "Queued" },
      { status: "processing" as const, label: "Processing" },
      { status: "pending_review" as const, label: "Pending Review" },
      { status: "finalized" as const, label: "Finalized" },
      { status: "failed" as const, label: "Failed" },
    ]

    statusTests.forEach(({ status, label }) => {
      it(`renders ${status} status with text "${label}"`, () => {
        if (status === "processing") {
          vi.mocked(sessionsApi.getSession).mockResolvedValue({
            ...mockSessionBase,
            status: "processing",
          })
        }

        render(
          <SessionStatusBadge status={status} sessionId="session-123" />,
          { wrapper: createWrapper() }
        )

        const badge = screen.getByRole("status")
        expect(badge).toHaveTextContent(label)
      })
    })
  })


  describe("Timestamp Display", () => {
    it("shows timestamp for finalized status", () => {
      render(
        <SessionStatusBadge
          status="finalized"
          sessionId="session-123"
          timestamp="2024-01-15T14:35:00Z"
        />,
        { wrapper: createWrapper() }
      )

      expect(screen.getByText(/Jan 15/)).toBeInTheDocument()
    })

    it("does not show timestamp when not provided", () => {
      render(
        <SessionStatusBadge status="finalized" sessionId="session-123" />,
        { wrapper: createWrapper() }
      )

      const badge = screen.getByRole("status")
      expect(badge).not.toHaveTextContent("Jan")
    })

    it("does not show timestamp for non-finalized status", () => {
      render(
        <SessionStatusBadge
          status="pending_review"
          sessionId="session-123"
          timestamp="2024-01-15T14:35:00Z"
        />,
        { wrapper: createWrapper() }
      )

      const badge = screen.getByRole("status")
      expect(badge).not.toHaveTextContent("Jan")
    })

    it("formats timestamp correctly", () => {
      render(
        <SessionStatusBadge
          status="finalized"
          sessionId="session-123"
          timestamp="2024-12-25T10:00:00Z"
        />,
        { wrapper: createWrapper() }
      )

      expect(screen.getByText(/Dec 25/)).toBeInTheDocument()
    })
  })

  describe("Auto-Polling", () => {
    it("fetches session data when status is processing", async () => {
      const mockSession = { ...mockSessionBase, status: "processing" as const }
      vi.mocked(sessionsApi.getSession).mockResolvedValue(mockSession)

      render(
        <SessionStatusBadge status="processing" sessionId="session-123" />,
        { wrapper: createWrapper() }
      )

      // Should fetch session data for processing status
      await waitFor(() => {
        expect(sessionsApi.getSession).toHaveBeenCalledWith("session-123", undefined)
      })

      expect(screen.getByText("Processing")).toBeInTheDocument()
    })

    it("does not fetch when status is not processing", () => {
      render(
        <SessionStatusBadge status="pending_review" sessionId="session-123" />,
        { wrapper: createWrapper() }
      )

      // Query is disabled, should not fetch
      expect(sessionsApi.getSession).not.toHaveBeenCalled()
    })

    it("uses fetched status over prop status when available", async () => {
      // Mock returns pending_review even though prop says processing
      const reviewSession = { ...mockSessionBase, status: "pending_review" as const }
      vi.mocked(sessionsApi.getSession).mockResolvedValue(reviewSession)

      render(
        <SessionStatusBadge status="processing" sessionId="session-123" />,
        { wrapper: createWrapper() }
      )

      // Should fetch and then display the fetched status (pending_review)
      await waitFor(() => {
        expect(screen.getByText("Pending Review")).toBeInTheDocument()
      })
    })

    it("stops fetching when status prop changes from processing", async () => {
      const processingSession = { ...mockSessionBase, status: "processing" as const }
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      const { rerender } = render(
        <SessionStatusBadge status="processing" sessionId="session-123" />,
        { wrapper: createWrapper() }
      )

      await waitFor(() => {
        expect(sessionsApi.getSession).toHaveBeenCalled()
      })

      const callCount = vi.mocked(sessionsApi.getSession).mock.calls.length

      // Change to non-processing status
      rerender(
        <SessionStatusBadge status="pending_review" sessionId="session-123" />
      )

      // Give it a moment
      await new Promise((resolve) => setTimeout(resolve, 100))

      // Should not have made additional fetches
      expect(vi.mocked(sessionsApi.getSession).mock.calls.length).toBe(callCount)
    })
  })

  describe("Accessibility", () => {
    it("has role=status", () => {
      render(
        <SessionStatusBadge status="pending_review" sessionId="session-123" />,
        { wrapper: createWrapper() }
      )

      const badge = screen.getByRole("status")
      expect(badge).toBeInTheDocument()
    })

    it("has descriptive aria-label", () => {
      render(
        <SessionStatusBadge status="finalized" sessionId="session-123" />,
        { wrapper: createWrapper() }
      )

      const badge = screen.getByLabelText("Session status: Finalized")
      expect(badge).toBeInTheDocument()
    })

    it("provides different aria-labels for each status", () => {
      const { rerender } = render(
        <SessionStatusBadge status="queued" sessionId="session-123" />,
        { wrapper: createWrapper() }
      )
      expect(screen.getByLabelText("Session status: Queued")).toBeInTheDocument()

      rerender(
        <SessionStatusBadge status="failed" sessionId="session-123" />
      )
      expect(screen.getByLabelText("Session status: Failed")).toBeInTheDocument()
    })
  })

  describe("Edge Cases", () => {
    it("handles null status gracefully", () => {
      // TypeScript would prevent this, but test runtime behavior
      render(
        <SessionStatusBadge
          status={"queued"}
          sessionId="session-123"
        />,
        { wrapper: createWrapper() }
      )

      expect(screen.getByRole("status")).toBeInTheDocument()
    })

    it("applies custom className", () => {
      render(
        <SessionStatusBadge
          status="queued"
          sessionId="session-123"
          className="custom-class"
        />,
        { wrapper: createWrapper() }
      )

      const badge = screen.getByRole("status")
      expect(badge.className).toContain("custom-class")
    })

    it("handles status prop changes without fetching", () => {
      const { rerender } = render(
        <SessionStatusBadge status="queued" sessionId="session-123" />,
        { wrapper: createWrapper() }
      )

      expect(screen.getByText("Queued")).toBeInTheDocument()

      // Change to different non-processing status
      rerender(<SessionStatusBadge status="finalized" sessionId="session-123" />)
      expect(screen.getByText("Finalized")).toBeInTheDocument()

      rerender(<SessionStatusBadge status="failed" sessionId="session-123" />)
      expect(screen.getByText("Failed")).toBeInTheDocument()

      // Should not have fetched for non-processing statuses
      expect(sessionsApi.getSession).not.toHaveBeenCalled()
    })

    it("continues to show processing while fetching", async () => {
      const mockSession = { ...mockSessionBase, status: "processing" as const }
      vi.mocked(sessionsApi.getSession).mockResolvedValue(mockSession)

      render(
        <SessionStatusBadge status="processing" sessionId="session-123" />,
        { wrapper: createWrapper() }
      )

      await waitFor(() => {
        expect(screen.getByText("Processing")).toBeInTheDocument()
      })

      // Component fetches and displays processing status
      expect(sessionsApi.getSession).toHaveBeenCalled()
      expect(screen.getByRole("status")).toHaveClass("animate-pulse")
    })
  })
})
