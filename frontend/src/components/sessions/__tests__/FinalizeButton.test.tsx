// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tests for FinalizeButton component
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { FinalizeButton } from "../FinalizeButton"
import type { SessionStatus, SOAPNoteModel } from "@/types/sessions"
import * as useSessions from "@/hooks/useSessions"

// Create wrapper with React Query provider
function createWrapper() {
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

describe("FinalizeButton", () => {
  const mockMutateAsync = vi.fn()
  const mockUseFinalizeSession = {
    mutateAsync: mockMutateAsync,
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
    data: undefined,
    reset: vi.fn(),
    mutate: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(useSessions, "useFinalizeSession").mockReturnValue(
      mockUseFinalizeSession as any
    )
  })

  const defaultProps = {
    sessionId: "session-123",
    status: "pending_review" as SessionStatus,
    qualityRating: 4,
  }

  describe("Rendering - Pending Review State", () => {
    it("renders finalize button for pending review session", () => {
      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })

      expect(screen.getByRole("button", { name: /finalize session/i })).toBeInTheDocument()
    })

    it("button is enabled when status is pending_review and rating is set", () => {
      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })

      const button = screen.getByRole("button", { name: /finalize session/i })
      expect(button).not.toBeDisabled()
    })

    it("renders check icon", () => {
      const { container } = render(<FinalizeButton {...defaultProps} />, {
        wrapper: createWrapper(),
      })

      const icon = container.querySelector("svg")
      expect(icon).toBeInTheDocument()
    })
  })

  describe("Rendering - Finalized State", () => {
    it("renders finalized badge when session is finalized", () => {
      render(<FinalizeButton {...defaultProps} status="finalized" />, {
        wrapper: createWrapper(),
      })

      expect(screen.getByRole("button", { name: /finalized/i })).toBeInTheDocument()
    })

    it("finalized badge is disabled", () => {
      render(<FinalizeButton {...defaultProps} status="finalized" />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalized/i })
      expect(button).toBeDisabled()
    })
  })

  describe("Disabled States", () => {
    it("is disabled when quality rating is null", () => {
      render(<FinalizeButton {...defaultProps} qualityRating={null} />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalize session/i })
      expect(button).toBeDisabled()
    })

    it("is disabled when status is queued", () => {
      render(<FinalizeButton {...defaultProps} status="queued" />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalize session/i })
      expect(button).toBeDisabled()
    })

    it("is disabled when status is processing", () => {
      render(<FinalizeButton {...defaultProps} status="processing" />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalize session/i })
      expect(button).toBeDisabled()
    })

    it("is disabled when status is failed", () => {
      render(<FinalizeButton {...defaultProps} status="failed" />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalize session/i })
      expect(button).toBeDisabled()
    })

    it("is disabled when mutation is pending", () => {
      vi.spyOn(useSessions, "useFinalizeSession").mockReturnValue({
        ...mockUseFinalizeSession,
        isPending: true,
      } as any)

      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })

      const button = screen.getByRole("button", { name: /finalizing/i })
      expect(button).toBeDisabled()
    })
  })

  describe("Loading State", () => {
    it("shows loading text when mutation is pending", () => {
      vi.spyOn(useSessions, "useFinalizeSession").mockReturnValue({
        ...mockUseFinalizeSession,
        isPending: true,
      } as any)

      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })

      expect(screen.getByRole("button", { name: /finalizing/i })).toBeInTheDocument()
    })

    it("shows spinner icon when pending", () => {
      vi.spyOn(useSessions, "useFinalizeSession").mockReturnValue({
        ...mockUseFinalizeSession,
        isPending: true,
      } as any)

      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })

      const button = screen.getByRole("button", { name: /finalizing/i })
      expect(button).toHaveTextContent("⏳")
    })
  })

  describe("Click Behavior", () => {
    it("calls finalize mutation with quality rating only", async () => {
      const user = userEvent.setup()
      mockMutateAsync.mockResolvedValue({})

      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })

      const button = screen.getByRole("button", { name: /finalize session/i })
      await user.click(button)

      expect(mockMutateAsync).toHaveBeenCalledWith({
        sessionId: "session-123",
        data: {
          quality_rating: 4,
        },
      })
    })

    it("calls finalize mutation with quality rating and edited SOAP", async () => {
      const user = userEvent.setup()
      mockMutateAsync.mockResolvedValue({})

      const editedSOAP: SOAPNoteModel = {
        subjective: "Edited subjective",
        objective: "Edited objective",
        assessment: "Edited assessment",
        plan: "Edited plan",
      }

      render(<FinalizeButton {...defaultProps} soapNoteEdited={editedSOAP} />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalize session/i })
      await user.click(button)

      expect(mockMutateAsync).toHaveBeenCalledWith({
        sessionId: "session-123",
        data: {
          quality_rating: 4,
          soap_note_edited: editedSOAP,
        },
      })
    })

    it("does not call mutation when quality rating is null", async () => {
      const user = userEvent.setup()

      render(<FinalizeButton {...defaultProps} qualityRating={null} />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalize session/i })
      await user.click(button)

      expect(mockMutateAsync).not.toHaveBeenCalled()
    })

    it("does not call mutation when status is not pending_review", async () => {
      const user = userEvent.setup()

      render(<FinalizeButton {...defaultProps} status="queued" />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalize session/i })
      await user.click(button)

      expect(mockMutateAsync).not.toHaveBeenCalled()
    })

    it("calls onSuccess callback after successful finalization", async () => {
      const user = userEvent.setup()
      const onSuccess = vi.fn()
      mockMutateAsync.mockResolvedValue({})

      render(<FinalizeButton {...defaultProps} onSuccess={onSuccess} />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalize session/i })
      await user.click(button)

      await vi.waitFor(() => {
        expect(onSuccess).toHaveBeenCalled()
      })
    })

    it("handles mutation error gracefully", async () => {
      const user = userEvent.setup()
      const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {})
      mockMutateAsync.mockRejectedValue(new Error("Network error"))

      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })

      const button = screen.getByRole("button", { name: /finalize session/i })
      await user.click(button)

      await vi.waitFor(() => {
        expect(consoleErrorSpy).toHaveBeenCalledWith(
          "Failed to finalize session"
        )
      })

      consoleErrorSpy.mockRestore()
    })
  })

  describe("Quality Rating Values", () => {
    it("accepts rating of 1", async () => {
      const user = userEvent.setup()
      mockMutateAsync.mockResolvedValue({})

      render(<FinalizeButton {...defaultProps} qualityRating={1} />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalize session/i })
      await user.click(button)

      expect(mockMutateAsync).toHaveBeenCalledWith({
        sessionId: "session-123",
        data: { quality_rating: 1 },
      })
    })

    it("accepts rating of 5", async () => {
      const user = userEvent.setup()
      mockMutateAsync.mockResolvedValue({})

      render(<FinalizeButton {...defaultProps} qualityRating={5} />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalize session/i })
      await user.click(button)

      expect(mockMutateAsync).toHaveBeenCalledWith({
        sessionId: "session-123",
        data: { quality_rating: 5 },
      })
    })
  })

  describe("Multiple Clicks", () => {
    it("prevents multiple submissions when pending", async () => {
      const user = userEvent.setup()
      mockMutateAsync.mockImplementation(
        () => new Promise((resolve) => setTimeout(resolve, 100))
      )

      vi.spyOn(useSessions, "useFinalizeSession").mockReturnValue({
        ...mockUseFinalizeSession,
        isPending: false,
      } as any)

      const { rerender } = render(<FinalizeButton {...defaultProps} />, {
        wrapper: createWrapper(),
      })

      const button = screen.getByRole("button", { name: /finalize session/i })
      await user.click(button)

      // Simulate isPending becoming true
      vi.spyOn(useSessions, "useFinalizeSession").mockReturnValue({
        ...mockUseFinalizeSession,
        isPending: true,
      } as any)

      rerender(<FinalizeButton {...defaultProps} />)

      const pendingButton = screen.getByRole("button", { name: /finalizing/i })
      expect(pendingButton).toBeDisabled()
    })
  })
})
