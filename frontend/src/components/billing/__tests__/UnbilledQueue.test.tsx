// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * UnbilledQueue Component Tests
 *
 * Covers the states the queue has to get right: nothing to show, a populated
 * list linking each row to its session, a row whose amount is unresolved
 * (no rate set anywhere) rendering as unknown rather than free, and the
 * claim affordance — offered beside "Charge card" only when the client has
 * coverage on file, replaced by the claim's state once one is on its way.
 *
 * Plus the two shapes a row takes: an uncovered client is charged the full
 * rate, and a covered one is offered their copay with the full rate tucked
 * behind "Charge a different amount".
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { UnbilledQueue } from "../UnbilledQueue"
import type { UnbilledSessionItem } from "@/types/billing"

const useUnbilledQueue = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useBilling", () => ({
  useUnbilledQueue: (...args: unknown[]) => useUnbilledQueue(...args),
}))

vi.mock("../claims/ClaimReviewDialog", () => ({
  ClaimReviewDialog: () => null,
}))

// The charge itself is `ChargeCopay`'s own test; here the row only has to
// offer it (or not).
vi.mock("@/hooks/usePayments", () => ({
  useCreateCharge: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

vi.mock("@/hooks/usePreferences", () => ({
  useUserTimeZone: () => "America/New_York",
  formatInUserTimeZone: (
    date: Date | string,
    timeZone: string,
    options: Intl.DateTimeFormatOptions,
  ) => new Date(date).toLocaleDateString("en-US", { ...options, timeZone }),
}))

function item(overrides: Partial<UnbilledSessionItem> = {}): UnbilledSessionItem {
  return {
    session_id: "sess-1",
    patient_id: "patient-1",
    patient_name: "Ada Early",
    session_date: "2026-06-10T14:00:00Z",
    amount_cents: 15000,
    currency: "usd",
    appointment_id: "appt-1",
    has_coverage: false,
    copay_cents: null,
    claim: null,
    ...overrides,
  }
}

describe("UnbilledQueue", () => {
  it("shows an empty state when there is nothing unbilled", () => {
    useUnbilledQueue.mockReturnValue({ data: { items: [] }, isLoading: false })
    render(<UnbilledQueue />)
    expect(screen.getByText("Nothing unbilled")).toBeInTheDocument()
  })

  it("lists each unbilled session with client, date and amount, linking to the session", () => {
    useUnbilledQueue.mockReturnValue({ data: { items: [item()] }, isLoading: false })
    render(<UnbilledQueue />)

    expect(screen.getByText("Ada Early")).toBeInTheDocument()
    expect(screen.getByText("$150.00")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /Ada Early/ })).toHaveAttribute(
      "href",
      "/dashboard/sessions/sess-1",
    )
  })

  it("states that amounts are what was charged and Stripe is the source of truth", () => {
    useUnbilledQueue.mockReturnValue({ data: { items: [item()] }, isLoading: false })
    render(<UnbilledQueue />)
    expect(screen.getByText(/Stripe is the source of truth/)).toBeInTheDocument()
  })

  it("renders an unresolved amount as unknown rather than free", () => {
    useUnbilledQueue.mockReturnValue({
      data: { items: [item({ amount_cents: null })] },
      isLoading: false,
    })
    render(<UnbilledQueue />)
    expect(screen.getByText("No rate set")).toBeInTheDocument()
  })
})

describe("UnbilledQueue claims", () => {
  it("offers Charge card but not File claim when the client has no coverage", () => {
    useUnbilledQueue.mockReturnValue({
      data: { items: [item({ has_coverage: false })] },
      isLoading: false,
    })
    render(<UnbilledQueue />)
    expect(screen.getByRole("link", { name: "Charge card" })).toHaveAttribute(
      "href",
      "/dashboard/sessions/sess-1",
    )
    expect(screen.queryByTestId("file-claim")).not.toBeInTheDocument()
  })

  it("offers File claim beside the copay when the client has coverage", () => {
    useUnbilledQueue.mockReturnValue({
      data: { items: [item({ has_coverage: true, copay_cents: 2500 })] },
      isLoading: false,
    })
    render(<UnbilledQueue />)
    expect(screen.getByTestId("charge-copay")).toHaveTextContent("Charge copay $25.00")
    expect(screen.getByTestId("file-claim")).toHaveTextContent("File claim")
  })

  it("does not offer a claim for a session that was never booked", () => {
    useUnbilledQueue.mockReturnValue({
      data: { items: [item({ has_coverage: true, appointment_id: null })] },
      isLoading: false,
    })
    render(<UnbilledQueue />)
    expect(screen.queryByTestId("file-claim")).not.toBeInTheDocument()
  })

  it("picks up a draft already on the visit with Review and file", () => {
    useUnbilledQueue.mockReturnValue({
      data: {
        items: [
          item({
            has_coverage: true,
            claim: { id: "c1", control_number: "88659891", state: "draft", frequency_code: "1" },
          }),
        ],
      },
      isLoading: false,
    })
    render(<UnbilledQueue />)
    expect(screen.getByTestId("file-claim")).toHaveTextContent("Review and file")
  })

  it("shows where a filed claim stands instead of offering to file again", () => {
    useUnbilledQueue.mockReturnValue({
      data: {
        items: [
          item({
            has_coverage: true,
            claim: {
              id: "c1",
              control_number: "88659891",
              state: "validated",
              frequency_code: "1",
            },
          }),
        ],
      },
      isLoading: false,
    })
    render(<UnbilledQueue />)
    expect(screen.queryByTestId("file-claim")).not.toBeInTheDocument()
    expect(screen.getByTestId("claim-state")).toHaveTextContent("Queued to send")
    expect(screen.getByTestId("queue-claim-link")).toHaveAttribute(
      "href",
      "/dashboard/billing/claims/c1",
    )
  })

  it("offers to file again once the last claim on the visit was voided", () => {
    useUnbilledQueue.mockReturnValue({
      data: {
        items: [
          item({
            has_coverage: true,
            claim: { id: "c2", control_number: "88659892", state: "submitted", frequency_code: "8" },
          }),
        ],
      },
      isLoading: false,
    })
    render(<UnbilledQueue />)
    expect(screen.getByTestId("file-claim")).toHaveTextContent("File claim")
  })
})

describe("UnbilledQueue copay", () => {
  it("offers no copay and the plain charge for a client with no coverage", () => {
    useUnbilledQueue.mockReturnValue({
      data: { items: [item({ has_coverage: false })] },
      isLoading: false,
    })
    render(<UnbilledQueue />)

    expect(screen.queryByTestId("charge-copay")).not.toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Charge card" })).toBeInTheDocument()
  })

  it("shows the copay amount on the button when the row carries one", () => {
    useUnbilledQueue.mockReturnValue({
      data: { items: [item({ has_coverage: true, copay_cents: 3000 })] },
      isLoading: false,
    })
    render(<UnbilledQueue />)

    expect(screen.getByTestId("charge-copay")).toHaveTextContent("Charge copay $30.00")
  })

  it("asks for the amount when nobody has said what the copay is", () => {
    useUnbilledQueue.mockReturnValue({
      data: { items: [item({ has_coverage: true, copay_cents: null })] },
      isLoading: false,
    })
    render(<UnbilledQueue />)

    expect(screen.getByTestId("charge-copay")).toHaveTextContent("Charge copay")
    expect(screen.getByTestId("charge-copay")).not.toHaveTextContent("$")
  })

  it("offers no copay when the payer priced the benefit at nothing", () => {
    useUnbilledQueue.mockReturnValue({
      data: { items: [item({ has_coverage: true, copay_cents: 0 })] },
      isLoading: false,
    })
    render(<UnbilledQueue />)

    expect(screen.queryByTestId("charge-copay")).not.toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Charge card" })).toBeInTheDocument()
  })

  it("keeps the full rate behind Charge a different amount for a covered client", async () => {
    const user = userEvent.setup()
    useUnbilledQueue.mockReturnValue({
      data: { items: [item({ has_coverage: true, copay_cents: 2500 })] },
      isLoading: false,
    })
    render(<UnbilledQueue />)

    expect(screen.queryByRole("link", { name: "Charge card" })).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Charge a different amount" }))

    expect(screen.getByRole("link", { name: "Charge card" })).toHaveAttribute(
      "href",
      "/dashboard/sessions/sess-1",
    )
  })
})
