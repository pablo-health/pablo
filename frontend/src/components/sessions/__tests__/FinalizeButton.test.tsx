// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tests for FinalizeButton: sign and lock a session's note after review.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { FinalizeButton } from "../FinalizeButton"
import type { SessionStatus, SOAPNoteModel } from "@/types/sessions"
import * as useSessions from "@/hooks/useSessions"

vi.mock("@/hooks/useNoteSigning", () => ({
  useSignerDefaults: () => ({ name: "Sam Ortiz", credentials: "LMFT" }),
}))
vi.mock("@/hooks/usePreferences", () => ({
  useUserTimeZone: () => "America/New_York",
}))

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
    mockUseFinalizeSession.isPending = false
    vi.spyOn(useSessions, "useFinalizeSession").mockReturnValue(
      mockUseFinalizeSession as never,
    )
  })

  const defaultProps = {
    sessionId: "session-123",
    status: "pending_review" as SessionStatus,
    qualityRating: 4,
  }

  async function signThroughDialog(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: /sign and lock/i }))
    const dialog = screen.getByRole("dialog")
    await user.click(within(dialog).getByRole("button", { name: "Sign and lock" }))
  }

  describe("Rendering", () => {
    it("offers Sign and lock for a session under review", () => {
      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })
      expect(screen.getByRole("button", { name: /sign and lock/i })).not.toBeDisabled()
    })

    it("is enabled with no rating (rating is optional)", () => {
      render(<FinalizeButton {...defaultProps} qualityRating={null} />, {
        wrapper: createWrapper(),
      })
      expect(screen.getByRole("button", { name: /sign and lock/i })).not.toBeDisabled()
    })

    it.each(["queued", "processing", "failed"] as SessionStatus[])(
      "is disabled when status is %s",
      (status) => {
        render(<FinalizeButton {...defaultProps} status={status} />, {
          wrapper: createWrapper(),
        })
        expect(screen.getByRole("button", { name: /sign and lock/i })).toBeDisabled()
      },
    )

    it("is disabled while the request is in flight", () => {
      mockUseFinalizeSession.isPending = true
      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })
      expect(screen.getByRole("button", { name: /sign and lock/i })).toBeDisabled()
    })

    it("renders a disabled Finalized badge once finalized", () => {
      render(<FinalizeButton {...defaultProps} status="finalized" />, {
        wrapper: createWrapper(),
      })
      expect(screen.getByRole("button", { name: /finalized/i })).toBeDisabled()
    })
  })

  describe("Signing", () => {
    it("prefills the profile and previews the block", async () => {
      const user = userEvent.setup()
      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })
      await user.click(screen.getByRole("button", { name: /sign and lock/i }))

      expect(screen.getByLabelText("Your name")).toHaveValue("Sam Ortiz")
      expect(screen.getByLabelText("Credentials")).toHaveValue("LMFT")
      const preview = screen.getByTestId("signature-preview")
      expect(preview).toHaveTextContent("Electronically signed by Sam Ortiz, LMFT")
      expect(preview).toHaveTextContent(/E[SD]T/)

      await user.clear(screen.getByLabelText("Credentials"))
      await user.type(screen.getByLabelText("Credentials"), "LMFT, LPCC")
      expect(preview).toHaveTextContent("Electronically signed by Sam Ortiz, LMFT, LPCC")
    })

    it("signs with the entered name and credentials, the rating and edited SOAP", async () => {
      const user = userEvent.setup()
      mockMutateAsync.mockResolvedValue({})
      const edited: SOAPNoteModel = {
        subjective: "S",
        objective: "O",
        assessment: "A",
        plan: "P",
      }
      render(
        <FinalizeButton
          {...defaultProps}
          qualityRatingReason="Clear"
          qualityRatingSections={["plan"]}
          soapNoteEdited={edited}
        />,
        { wrapper: createWrapper() },
      )
      await user.click(screen.getByRole("button", { name: /sign and lock/i }))
      await user.clear(screen.getByLabelText("Credentials"))
      await user.type(screen.getByLabelText("Credentials"), "PhD")
      await user.click(
        within(screen.getByRole("dialog")).getByRole("button", { name: "Sign and lock" }),
      )

      expect(mockMutateAsync).toHaveBeenCalledWith({
        sessionId: "session-123",
        data: {
          quality_rating: 4,
          quality_rating_reason: "Clear",
          quality_rating_sections: ["plan"],
          soap_note_edited: edited,
          signature: { signer_name: "Sam Ortiz", signer_credentials: "PhD" },
        },
      })
    })

    it("signs without a rating when none was given", async () => {
      const user = userEvent.setup()
      mockMutateAsync.mockResolvedValue({})
      render(<FinalizeButton {...defaultProps} qualityRating={null} />, {
        wrapper: createWrapper(),
      })
      await signThroughDialog(user)
      expect(mockMutateAsync).toHaveBeenCalledWith({
        sessionId: "session-123",
        data: { signature: { signer_name: "Sam Ortiz", signer_credentials: "LMFT" } },
      })
    })

    it("refuses a blank name", async () => {
      const user = userEvent.setup()
      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })
      await user.click(screen.getByRole("button", { name: /sign and lock/i }))
      await user.clear(screen.getByLabelText("Your name"))
      expect(
        within(screen.getByRole("dialog")).getByRole("button", { name: "Sign and lock" }),
      ).toBeDisabled()
    })

    it("calls onSuccess after signing", async () => {
      const user = userEvent.setup()
      const onSuccess = vi.fn()
      mockMutateAsync.mockResolvedValue({})
      render(<FinalizeButton {...defaultProps} onSuccess={onSuccess} />, {
        wrapper: createWrapper(),
      })
      await signThroughDialog(user)
      expect(onSuccess).toHaveBeenCalledTimes(1)
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    })

    it("keeps the dialog open and says so when signing fails", async () => {
      const user = userEvent.setup()
      const onSuccess = vi.fn()
      mockMutateAsync.mockRejectedValue(new Error("Note is already signed"))
      render(<FinalizeButton {...defaultProps} onSuccess={onSuccess} />, {
        wrapper: createWrapper(),
      })
      await signThroughDialog(user)
      expect(onSuccess).not.toHaveBeenCalled()
      expect(screen.getByRole("alert")).toHaveTextContent("Note is already signed")
    })
  })

  describe("read-only deployment mode", () => {
    afterEach(() => {
      vi.unstubAllEnvs()
    })

    it("hides the action for a session under review", () => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")
      render(<FinalizeButton {...defaultProps} />, { wrapper: createWrapper() })
      expect(screen.queryByRole("button", { name: /sign and lock/i })).not.toBeInTheDocument()
    })

    it("still shows the disabled Finalized status badge", () => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")
      render(<FinalizeButton {...defaultProps} status="finalized" />, {
        wrapper: createWrapper(),
      })
      expect(screen.getByRole("button", { name: /finalized/i })).toBeDisabled()
    })
  })
})
