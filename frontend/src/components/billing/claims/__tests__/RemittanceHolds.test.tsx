// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The therapist's side of a held remittance.
 *
 * The assertions that matter most are about what is never true: neither
 * answer is ever disabled, nothing has to be acknowledged first, and no
 * screen presents the payer's figure as settled. Each of those would put
 * the software between a practice and its own client's balance, and each
 * would be an easy thing to introduce while tidying the component.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import type { RemittanceHold } from "@/types/claims"
import { RemittanceHolds } from "../RemittanceHolds"

const resolve = vi.fn()
const acknowledge = vi.fn()
let holds: RemittanceHold[] = []
let loading = false

vi.mock("@/hooks/useClaims", () => ({
  useRemittanceHolds: () => ({ data: { data: holds, total: holds.length }, isLoading: loading }),
  useResolveRemittanceHold: () => ({ mutate: resolve, isPending: false }),
  useAcknowledgeRemittanceHold: () => ({ mutate: acknowledge, isPending: false }),
}))

function hold(overrides: Partial<RemittanceHold> = {}): RemittanceHold {
  return {
    id: "hold-1",
    claim_id: "claim-1",
    control_number: "CLM0001",
    state: "open",
    reason: "patient_responsibility",
    stated_cents: 3000,
    computed_cents: 0,
    delta_cents: 3000,
    patient_responsibility_cents: 3000,
    line_control_number: null,
    codes: [{ group_code: "CO", reason_code: "45" }],
    line_count: 1,
    payer_name: "Aetna",
    detected_at: "2026-09-08T12:00:00Z",
    acknowledged_at: null,
    resolved_at: null,
    finding: null,
    ...overrides,
  }
}

beforeEach(() => {
  resolve.mockReset()
  acknowledge.mockReset()
  holds = [hold()]
  loading = false
})

describe("RemittanceHolds", () => {
  it("says plainly that the client was not billed", () => {
    render(<RemittanceHolds />)

    expect(screen.getByTestId("remittance-hold")).toHaveTextContent(/not.*been billed/i)
  })

  it("shows both figures rather than only the gap", () => {
    render(<RemittanceHolds />)

    const card = screen.getByTestId("remittance-hold")
    expect(card).toHaveTextContent("$30.00")
    expect(card).toHaveTextContent("$0.00")
  })

  it("names the claim and the payer so the remittance can be found", () => {
    render(<RemittanceHolds />)

    expect(screen.getByTestId("remittance-hold")).toHaveTextContent("CLM0001")
    expect(screen.getByTestId("remittance-hold")).toHaveTextContent("Aetna")
  })

  it("bills the stated amount when the therapist says so", () => {
    render(<RemittanceHolds />)

    fireEvent.click(screen.getByRole("button", { name: /bill .* as stated/i }))

    expect(resolve).toHaveBeenCalledWith(
      { holdId: "hold-1", finding: "bill_as_stated" },
      expect.anything(),
    )
  })

  it("waives it when the therapist says so", () => {
    render(<RemittanceHolds />)

    fireEvent.click(screen.getByRole("button", { name: /waive/i }))

    expect(resolve).toHaveBeenCalledWith({ holdId: "hold-1", finding: "waived" }, expect.anything())
  })

  it("never disables either answer", () => {
    render(<RemittanceHolds />)

    expect(screen.getByRole("button", { name: /bill .* as stated/i })).toBeEnabled()
    expect(screen.getByRole("button", { name: /waive/i })).toBeEnabled()
  })

  it("still offers both answers after acknowledgement", () => {
    holds = [hold({ state: "acknowledged", acknowledged_at: "2026-09-09T09:00:00Z" })]
    render(<RemittanceHolds />)

    expect(screen.getByRole("button", { name: /bill .* as stated/i })).toBeEnabled()
    expect(screen.getByRole("button", { name: /waive/i })).toBeEnabled()
    expect(screen.getByText(/still waiting on a decision/i)).toBeInTheDocument()
  })

  it("acknowledging does not decide anything", () => {
    render(<RemittanceHolds />)

    fireEvent.click(screen.getByRole("button", { name: /seen this/i }))

    expect(acknowledge).toHaveBeenCalledWith({ holdId: "hold-1" })
    expect(resolve).not.toHaveBeenCalled()
  })

  it("shows the adjustment codes, which is what a person looks up first", () => {
    render(<RemittanceHolds />)

    expect(screen.getByText(/CO-45/)).toBeInTheDocument()
  })

  it("names the failing line when the disagreement is on one", () => {
    holds = [hold({ reason: "line_balance", line_control_number: "CLM0001L2" })]
    render(<RemittanceHolds />)

    expect(screen.getByTestId("remittance-hold")).toHaveTextContent("CLM0001L2")
  })

  it("renders nothing at all when nothing is held", () => {
    holds = []
    const { container } = render(<RemittanceHolds />)

    expect(container).toBeEmptyDOMElement()
  })

  it("tells the therapist when the decision did not go through, and that nothing was billed", () => {
    resolve.mockImplementation((_vars, opts) => opts?.onError?.(new Error("nope")))
    render(<RemittanceHolds />)

    fireEvent.click(screen.getByRole("button", { name: /bill .* as stated/i }))

    expect(screen.getByRole("alert")).toHaveTextContent(/nothing was billed/i)
  })
})
