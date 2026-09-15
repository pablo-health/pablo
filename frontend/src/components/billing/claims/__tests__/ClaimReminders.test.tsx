// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What a claim still needs a person to do, on the billing screen.
 *
 * Bug classes covered:
 *   * a failed fetch rendering as "nothing to do". Indistinguishable to a
 *     reader, and only one of them means the practice can stop looking.
 *   * an empty section with a zero in its heading. A badge that is almost
 *     always zero is one people stop reading, and it would sit above the
 *     tabs on every visit.
 *   * an overdue reminder that says so only in a colour. A colour is not a
 *     sentence, and this is the state where being wrong costs money.
 *   * the payer's own words being collapsed. Codes and instructions arrive on
 *     separate lines and stay that way.
 */

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { ClaimReminders } from "../ClaimReminders"
import type { ClaimReminder } from "@/types/claims"

const useClaimReminders = vi.hoisted(() => vi.fn())
const useCompleteClaimReminder = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useClaims", () => ({
  useClaimReminders: () => useClaimReminders(),
  useCompleteClaimReminder: () => useCompleteClaimReminder(),
}))

function reminder(overrides: Partial<ClaimReminder> = {}): ClaimReminder {
  return {
    id: crypto.randomUUID(),
    claim_id: crypto.randomUUID(),
    control_number: "PCN20260906ABC",
    kind: "denied",
    label: "Claim PCN20260 denied by Aetna, by 2026-12-05",
    due_date: null,
    notes: null,
    completed_at: null,
    ...overrides,
  }
}

function loaded(data: ClaimReminder[]) {
  useClaimReminders.mockReturnValue({ data: { data, total: data.length }, isLoading: false, isError: false })
}

beforeEach(() => {
  useClaimReminders.mockReset()
  useCompleteClaimReminder.mockReset()
  useCompleteClaimReminder.mockReturnValue({ mutate: vi.fn(), isPending: false })
})

describe("before the answer arrives", () => {
  it("a failure says so rather than looking like nothing to do", () => {
    useClaimReminders.mockReturnValue({ data: undefined, isLoading: false, isError: true })

    render(<ClaimReminders />)

    expect(screen.getByText(/couldn.t load/i)).toBeInTheDocument()
  })
})

describe("when no claim needs anything", () => {
  it("renders nothing at all, rather than a zero", () => {
    loaded([])

    const { container } = render(<ClaimReminders />)

    expect(container).toBeEmptyDOMElement()
  })
})

describe("when claims need something", () => {
  it("counts them in the heading", () => {
    loaded([reminder(), reminder({ kind: "rejected" })])

    render(<ClaimReminders />)

    expect(screen.getByText("2 claims need you")).toBeInTheDocument()
    expect(screen.getAllByTestId("claim-reminder")).toHaveLength(2)
  })

  it("says one in the singular, because '1 claims' is how software talks", () => {
    loaded([reminder()])

    render(<ClaimReminders />)

    expect(screen.getByText("A claim needs you")).toBeInTheDocument()
  })

  it("says a passed deadline in words, not only in a colour", () => {
    loaded([reminder({ due_date: "2020-01-15" })])

    render(<ClaimReminders />)

    expect(screen.getByText(/was due/i)).toBeInTheDocument()
  })

  it("says a future deadline as due, not overdue", () => {
    loaded([reminder({ due_date: "2099-01-15" })])

    render(<ClaimReminders />)

    expect(screen.getByText(/^due /i)).toBeInTheDocument()
    expect(screen.queryByText(/was due/i)).not.toBeInTheDocument()
  })

  it("shows a reminder with no deadline without inventing one", () => {
    loaded([reminder({ due_date: null })])

    render(<ClaimReminders />)

    expect(screen.queryByText(/due/i)).not.toBeInTheDocument()
  })

  it("keeps the payer's own words", () => {
    loaded([
      reminder({
        notes: "Precertification absent\nSign and return the EFT authorization form.",
      }),
    ])

    render(<ClaimReminders />)

    expect(screen.getByText(/Precertification absent/)).toBeInTheDocument()
    expect(screen.getByText(/EFT authorization form/)).toBeInTheDocument()
  })
})
