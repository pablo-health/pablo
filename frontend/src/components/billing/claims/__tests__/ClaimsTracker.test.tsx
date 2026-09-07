// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The tracker: one row per claim with the state badge, the alert for a
 * claim that needs a person, the next action and the deadline, each row
 * linking to the claim's page.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, within } from "@testing-library/react"
import { ClaimsTracker } from "../ClaimsTracker"
import { trackerItem, VENDOR_FINDING } from "./claimFixtures"

const mockUseClaims = vi.fn()

vi.mock("@/hooks/useClaims", () => ({
  useClaims: (...args: unknown[]) => mockUseClaims(...args),
}))

describe("ClaimsTracker", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("shows an empty state when nothing has been filed", () => {
    mockUseClaims.mockReturnValue({ data: { data: [], total: 0 }, isLoading: false })
    render(<ClaimsTracker />)
    expect(screen.getByText("No claims yet")).toBeInTheDocument()
  })

  it("lists each claim with client, payer, state and a link to its page", () => {
    mockUseClaims.mockReturnValue({
      data: { data: [trackerItem({ state: "validated" })], total: 1 },
      isLoading: false,
    })
    render(<ClaimsTracker />)
    const row = screen.getByTestId("claims-tracker-row")
    expect(row).toHaveAttribute("data-state", "validated")
    expect(within(row).getByText("Ada Early")).toBeInTheDocument()
    expect(within(row).getByText("Test Payer")).toBeInTheDocument()
    expect(within(row).getByTestId("claim-state")).toHaveTextContent("Queued to send")
    expect(within(row).getByRole("link", { name: "88659891" })).toHaveAttribute(
      "href",
      "/dashboard/billing/claims/claim-1",
    )
    expect(within(row).queryByTestId("claim-alert")).not.toBeInTheDocument()
  })

  it("flags a rejected claim and tells the person to fix and refile before the deadline", () => {
    mockUseClaims.mockReturnValue({
      data: {
        data: [
          trackerItem({
            state: "rejected",
            next_action: "correct_and_resubmit",
            deadlines: {
              filing: "2026-09-20",
              correction: null,
              appeal: null,
              applicable: "filing",
              days_left: 1,
            },
          }),
        ],
        total: 1,
      },
      isLoading: false,
    })
    render(<ClaimsTracker />)
    const row = screen.getByTestId("claims-tracker-row")
    expect(within(row).getByTestId("claim-alert")).toBeInTheDocument()
    expect(within(row).getByText("Fix and refile")).toBeInTheDocument()
    const deadline = within(row).getByTestId("claim-deadline")
    expect(deadline).toHaveTextContent("Fix and refile before Sep 20, 2026 (1 day)")
    expect(deadline).toHaveAttribute("data-tone", "danger")
  })

  it("labels a corrected claim as such", () => {
    mockUseClaims.mockReturnValue({
      data: { data: [trackerItem({ frequency_code: "7", state: "submitted" })], total: 1 },
      isLoading: false,
    })
    render(<ClaimsTracker />)
    expect(screen.getByText("Corrected claim")).toBeInTheDocument()
  })

  it("takes the Next column from the API, not from the claim's state", () => {
    // A queued claim whose filing attempt is already in flight: the state says
    // `validated`, and only the API knows it is on its way out.
    mockUseClaims.mockReturnValue({
      data: {
        data: [trackerItem({ state: "validated", next_action: "sending" })],
        total: 1,
      },
      isLoading: false,
    })
    render(<ClaimsTracker />)
    const row = screen.getByTestId("claims-tracker-row")
    expect(within(row).getByTestId("claim-state")).toHaveTextContent("Queued to send")
    expect(within(row).getByText("Sending")).toBeInTheDocument()
  })

  it("asks for nothing on a paid claim", () => {
    mockUseClaims.mockReturnValue({
      data: { data: [trackerItem({ state: "paid", next_action: null })], total: 1 },
      isLoading: false,
    })
    render(<ClaimsTracker />)
    expect(screen.getByTestId("claims-tracker-row").textContent).not.toMatch(/Review|Waiting|Fix/)
  })

  it("ages the row from the last receipt, not from the day it was built", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-09-10T15:00:00Z"))
    mockUseClaims.mockReturnValue({
      data: {
        data: [
          trackerItem({
            state: "submitted",
            created_at: "2026-09-01T15:00:00Z",
            last_receipt_at: "2026-09-08T15:00:00Z",
          }),
        ],
        total: 1,
      },
      isLoading: false,
    })
    render(<ClaimsTracker />)
    expect(within(screen.getByTestId("claims-tracker-row")).getByText("2 days")).toBeInTheDocument()
    vi.useRealTimers()
  })

  it("never shows the clearinghouse's own wording in a row", () => {
    mockUseClaims.mockReturnValue({
      data: { data: [trackerItem({ state: "rejected" })], total: 1 },
      isLoading: false,
    })
    render(<ClaimsTracker />)
    // The vendor's description can quote the field at fault, so the tracker
    // never carries it — the audited detail view is the only place it renders.
    expect(screen.getByTestId("claims-tracker-row").textContent).not.toContain(
      VENDOR_FINDING.description,
    )
  })

  it("asks for one state when the filter is set", () => {
    mockUseClaims.mockReturnValue({ data: { data: [], total: 0 }, isLoading: false })
    render(<ClaimsTracker />)
    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "denied" } })
    expect(mockUseClaims).toHaveBeenLastCalledWith({ state: "denied" })
  })
})
