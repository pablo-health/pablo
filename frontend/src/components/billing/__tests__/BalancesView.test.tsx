// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * BalancesView tests — Billing's collections list.
 *
 * The list answers "who has not paid", so what matters is that a client with
 * a balance is on it, the order is oldest first, a credit is shown as one
 * rather than as a negative number, and every row leads to the chart where
 * the ledger behind the figure lives.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

import { BalancesView } from "../BalancesView"
import type { ClientBalanceItem } from "@/types/payments"

const mockUseBalances = vi.fn()

vi.mock("@/hooks/useBilling", () => ({
  useBalances: () => mockUseBalances(),
}))

function item(overrides: Partial<ClientBalanceItem> = {}): ClientBalanceItem {
  return {
    patient_id: "patient-1",
    patient_name: "Jane Roe",
    balance_cents: 6200,
    currency: "usd",
    outstanding_since: "2026-05-24T10:00:00Z",
    ...overrides,
  }
}

function setup(items: ClientBalanceItem[]) {
  mockUseBalances.mockReturnValue({ data: { items }, isLoading: false, error: null })
  render(<BalancesView />)
}

describe("BalancesView", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("lists a client with a balance and links to their chart", () => {
    setup([item()])

    expect(screen.getByText("Jane Roe")).toBeInTheDocument()
    expect(screen.getByText("$62.00")).toBeInTheDocument()
    expect(screen.getByRole("link")).toHaveAttribute("href", "/dashboard/patients/patient-1")
  })

  it("keeps the server's order, which is oldest outstanding first", () => {
    setup([
      item({ patient_id: "p-1", patient_name: "Older Client" }),
      item({ patient_id: "p-2", patient_name: "Newer Client" }),
    ])

    const names = screen.getAllByRole("link").map((link) => link.textContent)
    expect(names[0]).toContain("Older Client")
    expect(names[1]).toContain("Newer Client")
  })

  it("shows a credit as a credit rather than as a negative number", () => {
    setup([item({ balance_cents: -1000 })])

    expect(screen.getByText("Credit $10.00")).toBeInTheDocument()
    expect(screen.queryByText("-$10.00")).not.toBeInTheDocument()
  })

  it("says so when every ledger nets to zero", () => {
    setup([])

    expect(screen.getByText("Nothing outstanding")).toBeInTheDocument()
  })

  it("reports a failed read rather than an empty list", () => {
    mockUseBalances.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("boom"),
    })

    render(<BalancesView />)

    expect(screen.getByText("boom")).toBeInTheDocument()
    expect(screen.queryByText("Nothing outstanding")).not.toBeInTheDocument()
  })
})
