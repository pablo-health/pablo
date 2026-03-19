// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SessionsTable Component Tests
 *
 * Comprehensive tests for session list display, navigation, and state handling.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { SessionsTable } from "../SessionsTable"
import * as sessionsApi from "@/lib/api/sessions"
import { createMockSession } from "@/test/factories"

vi.mock("@/lib/api/sessions")
vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

const mockPush = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
  }),
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

const mockSession = createMockSession({
  transcript: { format: "vtt", content: "Test" },
  soap_note: {
    subjective: "Test",
    objective: "Test",
    assessment: "Test",
    plan: "Test",
  },
})

describe("SessionsTable", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPush.mockClear()
  })

  describe("Loading State", () => {
    it("shows loading skeleton while fetching", () => {
      vi.mocked(sessionsApi.listSessions).mockImplementation(
        () => new Promise(() => {}) // Never resolves
      )

      render(<SessionsTable />, { wrapper: createWrapper() })

      expect(screen.getByLabelText("Loading sessions")).toBeInTheDocument()
      expect(screen.getAllByRole("status")).toHaveLength(1)
    })
  })

  describe("Error State", () => {
    it("displays error message when fetch fails", async () => {
      vi.mocked(sessionsApi.listSessions).mockRejectedValue(
        new Error("Network error")
      )

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("Failed to load sessions")).toBeInTheDocument()
      })

      expect(screen.getByText("Network error")).toBeInTheDocument()
    })

    it("shows retry button on error", async () => {
      vi.mocked(sessionsApi.listSessions).mockRejectedValue(new Error("Error"))

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("Try Again")).toBeInTheDocument()
      })
    })

    it("retries fetch when retry button is clicked", async () => {
      vi.mocked(sessionsApi.listSessions).mockRejectedValueOnce(new Error("Error"))

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("Try Again")).toBeInTheDocument()
      })

      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      fireEvent.click(screen.getByText("Try Again"))

      await waitFor(() => {
        expect(screen.getByText("Doe, Jane")).toBeInTheDocument()
      })
    })
  })

  describe("Empty State", () => {
    it("shows empty state when no sessions", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(
          screen.getByText(/No sessions found. Upload a transcript to get started./)
        ).toBeInTheDocument()
      })
    })
  })

  describe("Table Display", () => {
    it("renders table with sessions", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("Doe, Jane")).toBeInTheDocument()
      })

      expect(screen.getByText("Patient")).toBeInTheDocument()
      expect(screen.getByText("Date")).toBeInTheDocument()
      expect(screen.getByText("Session #")).toBeInTheDocument()
      expect(screen.getByText("Status")).toBeInTheDocument()
      expect(screen.getByText("Rating")).toBeInTheDocument()
    })

    it("displays patient name correctly", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("Doe, Jane")).toBeInTheDocument()
      })
    })

    it("displays formatted date correctly", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText(/Jan 15, 2024/)).toBeInTheDocument()
      })
    })

    it("displays session number", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("1")).toBeInTheDocument()
      })
    })

    it("renders SessionStatusBadge for each session", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("Pending Review")).toBeInTheDocument()
      })
    })

    it("renders QualityRating when rating exists", async () => {
      const ratedSession = {
        ...mockSession,
        quality_rating: 4,
      }

      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [ratedSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      const { container } = render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        const stars = container.querySelectorAll(".fill-amber-400")
        expect(stars.length).toBeGreaterThan(0)
      })
    })

    it("shows 'Not rated' when no quality rating", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("Not rated")).toBeInTheDocument()
      })
    })

    it("renders multiple sessions", async () => {
      const session2 = {
        ...mockSession,
        id: "session-456",
        patient_name: "Smith, John",
        session_number: 2,
      }

      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession, session2],
        total: 2,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("Doe, Jane")).toBeInTheDocument()
        expect(screen.getByText("Smith, John")).toBeInTheDocument()
      })
    })
  })

  describe("Navigation", () => {
    it("navigates to session detail on row click", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("Doe, Jane")).toBeInTheDocument()
      })

      const row = screen.getByRole("button", {
        name: "View session for Doe, Jane",
      })
      fireEvent.click(row)

      expect(mockPush).toHaveBeenCalledWith("/dashboard/sessions/session-123")
    })

    it("navigates on Enter key press", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("Doe, Jane")).toBeInTheDocument()
      })

      const row = screen.getByRole("button", {
        name: "View session for Doe, Jane",
      })
      fireEvent.keyDown(row, { key: "Enter" })

      expect(mockPush).toHaveBeenCalledWith("/dashboard/sessions/session-123")
    })

    it("navigates on Space key press", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText("Doe, Jane")).toBeInTheDocument()
      })

      const row = screen.getByRole("button", {
        name: "View session for Doe, Jane",
      })
      fireEvent.keyDown(row, { key: " " })

      expect(mockPush).toHaveBeenCalledWith("/dashboard/sessions/session-123")
    })
  })

  describe("Accessibility", () => {
    it("has accessible row labels", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(
          screen.getByRole("button", { name: "View session for Doe, Jane" })
        ).toBeInTheDocument()
      })
    })

    it("has keyboard navigation support", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [mockSession],
        total: 1,
        page: 1,
        page_size: 50,
      })

      render(<SessionsTable />, { wrapper: createWrapper() })

      await waitFor(() => {
        const row = screen.getByRole("button", {
          name: "View session for Doe, Jane",
        })
        expect(row).toHaveAttribute("tabIndex", "0")
      })
    })
  })

  describe("Edge Cases", () => {
    it("applies custom className", () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue({
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      })

      const { container } = render(
        <SessionsTable className="custom-class" />,
        { wrapper: createWrapper() }
      )

      waitFor(() => {
        const card = container.querySelector(".card")
        expect(card?.className).toContain("custom-class")
      })
    })
  })
})
