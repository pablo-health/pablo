// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The review step: opening builds a draft from the visit, "Review and
 * file" validates it into a queued claim, and a refusal lists the blocking
 * findings and keeps the button disabled.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ApiError } from "@/lib/api/client"
import { ClaimReviewDialog } from "../ClaimReviewDialog"
import { claimDetail } from "./claimFixtures"

const mockBuild = vi.fn()
const mockValidate = vi.fn()
const mockUseClaim = vi.fn()

vi.mock("@/hooks/useClaims", () => ({
  useBuildClaim: () => ({ mutateAsync: mockBuild, isPending: false }),
  useValidateClaim: () => ({ mutateAsync: mockValidate, isPending: false }),
  useClaim: (...args: unknown[]) => mockUseClaim(...args),
}))

describe("ClaimReviewDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBuild.mockResolvedValue({ id: "claim-1" })
    mockUseClaim.mockImplementation((claimId: string | undefined) => ({
      data: claimId ? claimDetail() : undefined,
      isLoading: false,
    }))
  })

  function renderOpen(props: Partial<React.ComponentProps<typeof ClaimReviewDialog>> = {}) {
    return render(
      <ClaimReviewDialog
        open
        onOpenChange={() => {}}
        appointmentId="appt-1"
        patientName="Ada Early"
        {...props}
      />,
    )
  }

  it("builds a draft from the visit when opened and shows it", async () => {
    renderOpen()
    await waitFor(() => expect(mockBuild).toHaveBeenCalledWith({ appointmentId: "appt-1" }))
    expect(await screen.findByText("Claim 88659891")).toBeInTheDocument()
    expect(screen.getByText("Nothing stops this claim from being filed.")).toBeInTheDocument()
    expect(screen.getByTestId("review-and-file")).toBeEnabled()
  })

  it("reviews the draft already on the visit instead of building another", async () => {
    renderOpen({ claimId: "claim-1" })
    expect(await screen.findByText("Claim 88659891")).toBeInTheDocument()
    expect(mockBuild).not.toHaveBeenCalled()
  })

  it("files the claim and reads Queued to send, never Sent", async () => {
    mockValidate.mockResolvedValue({ claim: claimDetail({ state: "validated" }), findings: [] })
    renderOpen()
    await userEvent.click(await screen.findByTestId("review-and-file"))
    await waitFor(() => expect(mockValidate).toHaveBeenCalledWith({ claimId: "claim-1" }))
    expect(screen.getByTestId("claim-state")).toHaveTextContent("Queued to send")
    expect(screen.getByRole("dialog").textContent).not.toMatch(/\bSent\b/)
    expect(screen.queryByTestId("review-and-file")).not.toBeInTheDocument()
  })

  it("lists the blocking findings on a refusal and disables filing", async () => {
    mockValidate.mockRejectedValue(
      new ApiError(
        "CLAIM_VALIDATION_FAILED",
        "The claim has blocking findings and stays a draft.",
        {
          findings: [
            {
              severity: "blocking",
              code: "dx_not_specific",
              message: "Diagnosis 1 is not a billable code.",
              field: "diagnosis_codes[0]",
            },
          ],
        },
        422,
      ),
    )
    renderOpen()
    await userEvent.click(await screen.findByTestId("review-and-file"))
    expect(await screen.findByText("Diagnosis 1 is not a billable code.")).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent("One thing stops this claim")
    expect(screen.getByTestId("review-and-file")).toBeDisabled()
  })

  it("says so when the claim cannot be built", async () => {
    mockBuild.mockRejectedValue(
      new ApiError("UNPROCESSABLE_ENTITY", "The client has no active coverage on file.", {}, 422),
    )
    renderOpen()
    expect(
      await screen.findByText("The client has no active coverage on file."),
    ).toBeInTheDocument()
    expect(screen.getByTestId("review-and-file")).toBeDisabled()
  })
})
