// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * UnbilledQueue Component Tests
 *
 * Covers the states the queue has to get right: nothing to show, a populated
 * list linking each row to its session, a row whose amount is unresolved
 * (no rate set anywhere) rendering as unknown rather than free, and the
 * claim affordance — offered beside "Charge card" only when the client has
 * coverage on file, replaced by the claim's state once one is on its way.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { UnbilledQueue } from "../UnbilledQueue"
import type { UnbilledSessionItem } from "@/types/billing"

const useUnbilledQueue = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useBilling", () => ({
  useUnbilledQueue: (...args: unknown[]) => useUnbilledQueue(...args),
}))

vi.mock("../claims/ClaimReviewDialog", () => ({
  ClaimReviewDialog: () => null,
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

  it("offers File claim beside Charge card when the client has coverage", () => {
    useUnbilledQueue.mockReturnValue({
      data: { items: [item({ has_coverage: true })] },
      isLoading: false,
    })
    render(<UnbilledQueue />)
    expect(screen.getByRole("link", { name: "Charge card" })).toBeInTheDocument()
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
