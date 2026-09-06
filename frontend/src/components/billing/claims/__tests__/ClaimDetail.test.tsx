// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The claim detail's actions, gated by state: a draft is reviewed and
 * filed; a claim that has left the practice is corrected or voided; a
 * queued claim and a void offer neither. Plus the refusal path — a
 * blocking finding disables filing and is listed.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ApiError } from "@/lib/api/client"
import { ClaimDetail } from "../ClaimDetail"
import { claimDetail, hops } from "./claimFixtures"

const mockUseClaim = vi.fn()
const mockValidate = vi.fn()
const mockCorrect = vi.fn()
const mockVoid = vi.fn()
const mockPush = vi.fn()

vi.mock("@/hooks/useClaims", () => ({
  useClaim: (...args: unknown[]) => mockUseClaim(...args),
  useValidateClaim: () => ({ mutateAsync: mockValidate, isPending: false }),
  useCorrectClaim: () => ({ mutateAsync: mockCorrect, isPending: false }),
  useVoidClaim: () => ({ mutateAsync: mockVoid, isPending: false }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}))

describe("ClaimDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function renderState(overrides: Parameters<typeof claimDetail>[0]) {
    mockUseClaim.mockReturnValue({ data: claimDetail(overrides), isLoading: false, error: null })
    return render(<ClaimDetail claimId="claim-1" />)
  }

  it("offers Review and file, and nothing else, on a draft", () => {
    renderState({ state: "draft" })
    expect(screen.getByTestId("review-and-file")).toBeEnabled()
    expect(screen.queryByTestId("correct-claim")).not.toBeInTheDocument()
    expect(screen.queryByTestId("void-claim")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /CMS-1500/ })).not.toBeInTheDocument()
  })

  it("disables filing while a draft has a blocking finding", () => {
    renderState({
      state: "draft",
      findings: [
        {
          severity: "blocking",
          code: "dx_not_specific",
          message: "Diagnosis 1 is not a billable code.",
          field: "diagnosis_codes[0]",
        },
      ],
    })
    expect(screen.getByTestId("review-and-file")).toBeDisabled()
    expect(screen.getByText("Diagnosis 1 is not a billable code.")).toBeInTheDocument()
  })

  it("offers no action on a queued claim and never says Sent", () => {
    renderState({ state: "validated" })
    expect(screen.queryByTestId("review-and-file")).not.toBeInTheDocument()
    expect(screen.queryByTestId("correct-claim")).not.toBeInTheDocument()
    expect(screen.queryByTestId("void-claim")).not.toBeInTheDocument()
    expect(screen.getByTestId("claim-state")).toHaveTextContent("Queued to send")
    expect(screen.getByTestId("claim-detail").textContent).not.toMatch(/\bSent\b/)
  })

  it.each(["submitted", "ch_accepted", "payer_accepted", "paid", "partial", "denied", "rejected", "stalled"] as const)(
    "offers Correct and resubmit and Void on a %s claim",
    (state) => {
      renderState({ state, hops: hops(1), submitted_at: "2026-09-03T10:00:00Z" })
      expect(screen.getByTestId("correct-claim")).toBeEnabled()
      expect(screen.getByTestId("void-claim")).toBeEnabled()
      expect(screen.queryByTestId("review-and-file")).not.toBeInTheDocument()
    },
  )

  it("offers neither correction nor void on a void", () => {
    renderState({ state: "submitted", frequency_code: "8", parent_claim_id: "claim-0" })
    expect(screen.queryByTestId("correct-claim")).not.toBeInTheDocument()
    expect(screen.queryByTestId("void-claim")).not.toBeInTheDocument()
    expect(screen.getByRole("link", { name: "the original claim" })).toHaveAttribute(
      "href",
      "/dashboard/billing/claims/claim-0",
    )
  })

  it("shows the rejected claim's refile deadline", () => {
    renderState({
      state: "rejected",
      deadlines: {
        filing: "2026-09-20",
        correction: null,
        appeal: null,
        applicable: "filing",
        days_left: 5,
      },
    })
    expect(screen.getByTestId("claim-deadline")).toHaveTextContent(
      "Fix and refile before Sep 20, 2026 (5 days)",
    )
  })

  it("builds a corrected claim and goes to it", async () => {
    mockCorrect.mockResolvedValue({ id: "claim-2" })
    renderState({ state: "denied" })
    await userEvent.click(screen.getByTestId("correct-claim"))
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/dashboard/billing/claims/claim-2"))
    expect(mockCorrect).toHaveBeenCalledWith({ claimId: "claim-1" })
  })

  it("asks before voiding, then files the void", async () => {
    mockVoid.mockResolvedValue({ id: "claim-3" })
    renderState({ state: "payer_accepted" })
    await userEvent.click(screen.getByTestId("void-claim"))
    expect(mockVoid).not.toHaveBeenCalled()
    await userEvent.click(screen.getByTestId("confirm-void"))
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/dashboard/billing/claims/claim-3"))
  })

  it("lists the findings that stopped a filing and disables the button", async () => {
    mockValidate.mockRejectedValue(
      new ApiError(
        "CLAIM_VALIDATION_FAILED",
        "The claim has blocking findings and stays a draft.",
        {
          findings: [
            {
              severity: "blocking",
              code: "pos_telehealth_mismatch",
              message: "Place of service does not match a telehealth visit.",
              field: "place_of_service",
            },
          ],
        },
        422,
      ),
    )
    renderState({ state: "draft" })
    await userEvent.click(screen.getByTestId("review-and-file"))
    await waitFor(() =>
      expect(
        screen.getByText("Place of service does not match a telehealth visit."),
      ).toBeInTheDocument(),
    )
    expect(screen.getByTestId("review-and-file")).toBeDisabled()
  })

  it("reads adjudication as pending until a remittance is posted", () => {
    renderState({ state: "payer_accepted" })
    expect(screen.getAllByText("Pending").length).toBeGreaterThan(0)
    expect(screen.getByText("Pending adjudication")).toBeInTheDocument()
  })
})
