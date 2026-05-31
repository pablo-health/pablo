// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SessionGeneratingOverlay Component Tests
 *
 * Covers rendering states (processing / failed / hidden), rotating copy
 * cycling, auto-navigation when pending_review, and the dismiss / view-session
 * actions on failure.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor, act } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { SessionGeneratingOverlay } from "../SessionGeneratingOverlay"
import * as sessionsApi from "@/lib/api/sessions"
import * as sessionsHooks from "@/hooks/useSessions"
import { createMockSession } from "@/test/factories"

vi.mock("@/lib/api/sessions")
vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))
vi.mock("next/image", () => ({
  default: ({ src, alt, ...props }: { src: string; alt: string; [k: string]: unknown }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt={alt} {...props} />
  ),
}))

const mockPush = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
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

const processingSession = createMockSession({
  id: "session-abc",
  patient_id: "patient-1",
  status: "processing",
})

const pendingSession = createMockSession({
  id: "session-abc",
  patient_id: "patient-1",
  status: "pending_review",
})

const failedSession = createMockSession({
  id: "session-abc",
  patient_id: "patient-1",
  status: "failed",
})

const processingListResponse = {
  data: [processingSession],
  total: 1,
  page: 1,
  page_size: 50,
}

describe("SessionGeneratingOverlay", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPush.mockClear()
  })

  describe("Visibility", () => {
    it("renders nothing when patientId is null", () => {
      const { container } = render(
        <SessionGeneratingOverlay patientId={null} />,
        { wrapper: createWrapper() },
      )
      expect(container.firstChild).toBeNull()
    })

    it("renders the overlay when patientId is provided", () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      expect(screen.getByRole("status")).toBeInTheDocument()
    })

    it("shows the Pablo image", () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      const img = screen.getByAltText("Pablo")
      expect(img).toBeInTheDocument()
      expect(img).toHaveAttribute("src", "/pablo-today.webp")
    })
  })

  describe("Dismissible while processing", () => {
    it("renders a Close button and a Continue in the background button", () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(<SessionGeneratingOverlay patientId="patient-1" />, {
        wrapper: createWrapper(),
      })

      expect(screen.getByRole("button", { name: /close/i })).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: /continue in the background/i }),
      ).toBeInTheDocument()
    })

    it("calls onDone when Continue in the background is clicked", async () => {
      const user = userEvent.setup()
      const onDone = vi.fn()
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(<SessionGeneratingOverlay patientId="patient-1" onDone={onDone} />, {
        wrapper: createWrapper(),
      })

      await user.click(
        screen.getByRole("button", { name: /continue in the background/i }),
      )

      expect(onDone).toHaveBeenCalled()
    })

    it("calls onDone when the Close button is clicked", async () => {
      const user = userEvent.setup()
      const onDone = vi.fn()
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(<SessionGeneratingOverlay patientId="patient-1" onDone={onDone} />, {
        wrapper: createWrapper(),
      })

      await user.click(screen.getByRole("button", { name: /close/i }))

      expect(onDone).toHaveBeenCalled()
    })

    it("calls onDone when Escape is pressed", async () => {
      const user = userEvent.setup()
      const onDone = vi.fn()
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(<SessionGeneratingOverlay patientId="patient-1" onDone={onDone} />, {
        wrapper: createWrapper(),
      })

      await user.keyboard("{Escape}")

      expect(onDone).toHaveBeenCalled()
    })

    it("does not navigate when dismissed mid-processing", async () => {
      const user = userEvent.setup()
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(<SessionGeneratingOverlay patientId="patient-1" />, {
        wrapper: createWrapper(),
      })

      await user.click(
        screen.getByRole("button", { name: /continue in the background/i }),
      )

      expect(mockPush).not.toHaveBeenCalled()
    })
  })

  describe("Processing state", () => {
    it("shows the main heading", () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      expect(screen.getByText("Pablo is writing your note")).toBeInTheDocument()
    })

    it("shows a spinner", () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      // The Loader2 icon SVG is aria-hidden; find by the animate-spin class.
      const spinner = document.querySelector(".animate-spin")
      expect(spinner).toBeInTheDocument()
    })

    it("shows a rotating copy line", () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      // First copy line should be visible.
      expect(screen.getByText("Reading between the lines…")).toBeInTheDocument()
    })

    it("cycles copy after the interval", () => {
      vi.useFakeTimers()
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      expect(screen.getByText("Reading between the lines…")).toBeInTheDocument()

      act(() => {
        vi.advanceTimersByTime(3500)
      })

      // Second copy line should now be visible.
      expect(
        screen.getByText("Connecting the threads of your conversation…"),
      ).toBeInTheDocument()

      vi.useRealTimers()
    })
  })

  describe("Navigation on completion", () => {
    it("navigates to session detail when session reaches pending_review", async () => {
      // List returns a processing session first.
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      // Detail immediately returns pending_review.
      vi.mocked(sessionsApi.getSession).mockResolvedValue(pendingSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith("/dashboard/sessions/session-abc")
      })
    })

    it("calls onDone callback before navigating", async () => {
      const onDone = vi.fn()
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(pendingSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" onDone={onDone} />,
        { wrapper: createWrapper() },
      )

      await waitFor(() => {
        expect(onDone).toHaveBeenCalled()
      })
    })
  })

  describe("Failed state", () => {
    it("shows the error heading when session failed", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(failedSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      await waitFor(() => {
        expect(
          screen.getByText("Note generation didn't complete"),
        ).toBeInTheDocument()
      })
    })

    it("does not navigate on failure", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(failedSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      await waitFor(() => {
        expect(screen.getByText("Note generation didn't complete")).toBeInTheDocument()
      })

      expect(mockPush).not.toHaveBeenCalled()
    })

    it("shows dismiss and view-session buttons", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(failedSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /dismiss/i })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /view session/i })).toBeInTheDocument()
      })
    })

    it("calls onDone when dismiss is clicked", async () => {
      const user = userEvent.setup()
      const onDone = vi.fn()
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(failedSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" onDone={onDone} />,
        { wrapper: createWrapper() },
      )

      const dismissBtn = await screen.findByRole("button", { name: /dismiss/i })
      await user.click(dismissBtn)

      expect(onDone).toHaveBeenCalled()
    })

    it("navigates to session when view-session is clicked", async () => {
      const user = userEvent.setup()
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(failedSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      const viewBtn = await screen.findByRole("button", { name: /view session/i })
      await user.click(viewBtn)

      expect(mockPush).toHaveBeenCalledWith("/dashboard/sessions/session-abc")
    })
  })

  describe("Timed-out state", () => {
    afterEach(() => {
      vi.restoreAllMocks()
    })

    it("shows error card with Dismiss and no View session button when timedOut and no session", async () => {
      // Drive timedOut=true directly by spying on the hook
      vi.spyOn(sessionsHooks, "useSessionProcessing").mockReturnValue({
        sessionId: null,
        timedOut: true,
      })
      // useSession will be called with "__none__" (sessionId is null) but stub it anyway
      vi.spyOn(sessionsHooks, "useSession").mockReturnValue(
        { data: undefined, isLoading: false, isError: false } as never,
      )

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      await waitFor(() => {
        expect(screen.getByText("Note generation didn't complete")).toBeInTheDocument()
      })

      // Dismiss button present
      expect(screen.getByRole("button", { name: /dismiss/i })).toBeInTheDocument()
      // View session button must NOT be present (no session id)
      expect(screen.queryByRole("button", { name: /view session/i })).not.toBeInTheDocument()
    })
  })

  describe("Accessibility", () => {
    it("has role=status on the overlay container", () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      expect(screen.getByRole("status")).toBeInTheDocument()
    })

    it("has a descriptive aria-label during processing", () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(processingSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      expect(screen.getByLabelText("Generating SOAP note")).toBeInTheDocument()
    })

    it("has a descriptive aria-label when failed", async () => {
      vi.mocked(sessionsApi.listSessions).mockResolvedValue(processingListResponse)
      vi.mocked(sessionsApi.getSession).mockResolvedValue(failedSession)

      render(
        <SessionGeneratingOverlay patientId="patient-1" />,
        { wrapper: createWrapper() },
      )

      await waitFor(() => {
        expect(screen.getByLabelText("Note generation failed")).toBeInTheDocument()
      })
    })
  })
})
