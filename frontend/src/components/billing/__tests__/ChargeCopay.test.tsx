// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChargeCopay Component Tests
 *
 * The three amounts a copay can come from, and what each one sends: a figure
 * the row already carries (charged without echoing it back, so the server
 * resolves it again), a figure the clinician types when nobody has said, and
 * nothing at all — which must ask rather than charge. Plus the two outcomes
 * that are not an exception: a decline, and a success.
 */

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ChargeCopay } from "../ChargeCopay"
import type { UnbilledSessionItem } from "@/types/billing"

const mutateAsync = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/usePayments", () => ({
  useCreateCharge: () => ({ mutateAsync, isPending: false }),
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
    has_coverage: true,
    copay_cents: 2500,
    claim: null,
    ...overrides,
  }
}

function charged(overrides: Record<string, unknown> = {}) {
  return {
    id: "charge-1",
    amount_cents: 2500,
    currency: "usd",
    status: "succeeded",
    status_detail: null,
    appointment_id: "appt-1",
    created_at: "2026-06-10T14:05:00Z",
    updated_at: "2026-06-10T14:05:01Z",
    ...overrides,
  }
}

beforeEach(() => {
  mutateAsync.mockReset()
})

describe("ChargeCopay", () => {
  it("charges the copay against the visit without echoing the amount back", async () => {
    const user = userEvent.setup()
    mutateAsync.mockResolvedValue(charged())
    render(<ChargeCopay item={item()} />)

    await user.click(screen.getByTestId("charge-copay"))

    expect(mutateAsync).toHaveBeenCalledWith({
      patientId: "patient-1",
      data: { kind: "copay", appointment_id: "appt-1", amount_cents: undefined },
    })
  })

  it("confirms what was collected once the charge succeeds", async () => {
    const user = userEvent.setup()
    mutateAsync.mockResolvedValue(charged())
    render(<ChargeCopay item={item()} />)

    await user.click(screen.getByTestId("charge-copay"))

    expect(await screen.findByTestId("copay-charged")).toHaveTextContent("Copay $25.00 charged")
  })

  it("asks for the amount when nobody has said what the copay is", async () => {
    const user = userEvent.setup()
    render(<ChargeCopay item={item({ copay_cents: null })} />)

    await user.click(screen.getByTestId("charge-copay"))

    expect(await screen.findByText("What is the copay?")).toBeInTheDocument()
    // Asking is not charging.
    expect(mutateAsync).not.toHaveBeenCalled()
  })

  it("charges the amount the clinician typed", async () => {
    const user = userEvent.setup()
    mutateAsync.mockResolvedValue(charged({ amount_cents: 2000 }))
    render(<ChargeCopay item={item({ copay_cents: null })} />)

    await user.click(screen.getByTestId("charge-copay"))
    await user.type(await screen.findByLabelText("Amount"), "20")
    await user.click(screen.getByTestId("charge-typed-copay"))

    expect(mutateAsync).toHaveBeenCalledWith({
      patientId: "patient-1",
      data: { kind: "copay", appointment_id: "appt-1", amount_cents: 2000 },
    })
  })

  it("will not charge a half-typed amount", async () => {
    const user = userEvent.setup()
    render(<ChargeCopay item={item({ copay_cents: null })} />)

    await user.click(screen.getByTestId("charge-copay"))
    await screen.findByText("What is the copay?")

    expect(screen.getByTestId("charge-typed-copay")).toBeDisabled()
  })

  it("shows the reason a declined card gave", async () => {
    const user = userEvent.setup()
    mutateAsync.mockResolvedValue(
      charged({ status: "failed", status_detail: "insufficient_funds" }),
    )
    render(<ChargeCopay item={item()} />)

    await user.click(screen.getByTestId("charge-copay"))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The card has insufficient funds.",
    )
  })

  it("says what went wrong when the charge could not be attempted", async () => {
    const user = userEvent.setup()
    mutateAsync.mockRejectedValue(new Error("network"))
    render(<ChargeCopay item={item()} />)

    await user.click(screen.getByTestId("charge-copay"))

    expect(await screen.findByRole("alert")).toHaveTextContent("could not be charged")
  })
})
