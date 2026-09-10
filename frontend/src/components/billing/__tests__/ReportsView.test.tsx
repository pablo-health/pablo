// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ReportsView tests — the practice's financial report.
 *
 * What matters here: each of the four sections renders its own empty state
 * rather than a blank space or a division-by-zero when the practice has
 * nothing behind it, and the figures it does render come straight from the
 * response rather than being re-derived.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

import { ReportsView } from "../ReportsView"
import type { BillingReportResponse } from "@/types/billingReport"

const mockUseBillingReport = vi.fn()

vi.mock("@/hooks/useBilling", () => ({
  useBillingReport: () => mockUseBillingReport(),
}))

function emptyReport(): BillingReportResponse {
  return {
    from_date: "2026-09-01",
    to_date: "2026-09-30",
    aging: { buckets: [
      { label: "0-30", count: 0, cents: 0 },
      { label: "31-60", count: 0, cents: 0 },
      { label: "61-90", count: 0, cents: 0 },
      { label: "90+", count: 0, cents: 0 },
    ] },
    payer_mix: { entries: [] },
    collections_rate: {
      billed_cents: 0,
      collected_cents: 0,
      contractual_adjustment_cents: 0,
      write_off_cents: 0,
    },
    claim_payment_lag: { by_payer: [] },
  }
}

function setup(data: BillingReportResponse) {
  mockUseBillingReport.mockReturnValue({ data, isLoading: false, error: null })
  render(<ReportsView />)
}

describe("ReportsView", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("shows every section's empty state for an empty practice", () => {
    setup(emptyReport())

    expect(screen.getByText(/Nothing is currently owed/)).toBeInTheDocument()
    expect(screen.getByText(/No claims billed to a payer/)).toBeInTheDocument()
    expect(screen.getByText(/Nothing was billed in this window/)).toBeInTheDocument()
    expect(screen.getByText(/No paid claims in this window/)).toBeInTheDocument()
  })

  it("renders the aging buckets it is given", () => {
    const report = emptyReport()
    report.aging.buckets[0] = { label: "0-30", count: 2, cents: 15000 }
    setup(report)

    expect(screen.getByText("$150.00")).toBeInTheDocument()
    expect(screen.getByText("2 bills")).toBeInTheDocument()
  })

  it("renders payer mix rows by payer", () => {
    const report = emptyReport()
    report.payer_mix.entries = [
      { payer_id: "p1", payer_name: "Aetna", billed_cents: 15000, collected_cents: 10000 },
    ]
    setup(report)

    expect(screen.getByText("Aetna")).toBeInTheDocument()
    expect(screen.getByText("$150.00")).toBeInTheDocument()
    expect(screen.getByText("$100.00")).toBeInTheDocument()
  })

  it("computes the collections rate from cents, excluding write-offs and adjustments", () => {
    const report = emptyReport()
    report.collections_rate = {
      billed_cents: 10000,
      collected_cents: 7000,
      contractual_adjustment_cents: 0,
      write_off_cents: 3000,
    }
    setup(report)

    // collected 7000 / collectible (10000 - 0 - 3000 = 7000) = 100%
    expect(screen.getByText("100.0%")).toBeInTheDocument()
  })

  it("renders claim-to-payment lag per payer", () => {
    const report = emptyReport()
    report.claim_payment_lag.by_payer = [
      { payer_id: "p1", payer_name: "Cigna", claim_count: 3, median_days: 14, p90_days: 21 },
    ]
    setup(report)

    expect(screen.getByText("Cigna")).toBeInTheDocument()
    expect(screen.getByText("14.0")).toBeInTheDocument()
    expect(screen.getByText("21.0")).toBeInTheDocument()
  })

  it("reports a failed read", () => {
    mockUseBillingReport.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("boom"),
    })

    render(<ReportsView />)

    expect(screen.getByText("boom")).toBeInTheDocument()
  })
})
