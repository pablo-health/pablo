// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PaymentsTab tests — the chart's card-on-file and ledger surface.
 *
 * Covers the states a practice actually meets: no card yet, a stored card
 * rendered from the three display fields that exist, a declined row that keeps
 * its reason, and a deployment with no card processing configured, which is a
 * fact to state rather than an error to report.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen } from "@testing-library/react"

import { PaymentsTab } from "../PaymentsTab"
import { ApiError } from "@/lib/api/client"
import type { CardOnFileResponse, ChargeResponse } from "@/types/payments"

const mockUsePatientCard = vi.fn()
const mockUsePatientCharges = vi.fn()

vi.mock("@/hooks/usePayments", () => ({
  usePatientCard: (...args: unknown[]) => mockUsePatientCard(...args),
  usePatientCharges: (...args: unknown[]) => mockUsePatientCharges(...args),
}))

vi.mock("../AddCardDialog", () => ({
  AddCardDialog: () => null,
}))

const CARD: CardOnFileResponse = {
  brand: "visa",
  last4: "4242",
  exp_month: 4,
  exp_year: 2030,
  chargeable: true,
}

function charge(overrides: Partial<ChargeResponse> = {}): ChargeResponse {
  return {
    id: "charge-1",
    amount_cents: 15000,
    currency: "usd",
    status: "succeeded",
    status_detail: null,
    appointment_id: null,
    created_at: "2026-05-24T10:00:00Z",
    updated_at: null,
    ...overrides,
  }
}

describe("PaymentsTab", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUsePatientCard.mockReturnValue({ data: CARD, isLoading: false, error: null })
    mockUsePatientCharges.mockReturnValue({
      data: [charge()],
      isLoading: false,
      error: null,
    })
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it("renders the stored card and the ledger", () => {
    render(<PaymentsTab patientId="patient-1" />)

    expect(screen.getByText("Visa •••• 4242")).toBeInTheDocument()
    expect(screen.getByText("Expires 04/2030")).toBeInTheDocument()
    expect(screen.getByText("$150.00")).toBeInTheDocument()
    expect(screen.getByText("Paid")).toBeInTheDocument()
  })

  it("offers to add a card when there is none", () => {
    mockUsePatientCard.mockReturnValue({ data: null, isLoading: false, error: null })
    mockUsePatientCharges.mockReturnValue({ data: [], isLoading: false, error: null })

    render(<PaymentsTab patientId="patient-1" />)

    expect(screen.getByText("No card on file for this client.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add a card" })).toBeInTheDocument()
    expect(screen.getByText("No charges yet.")).toBeInTheDocument()
  })

  it("keeps a declined attempt on the ledger, with its reason", () => {
    mockUsePatientCharges.mockReturnValue({
      data: [charge({ status: "failed", status_detail: "expired_card" })],
      isLoading: false,
      error: null,
    })

    render(<PaymentsTab patientId="patient-1" />)

    expect(screen.getByText("Declined")).toBeInTheDocument()
    expect(screen.getByText("The card has expired.")).toBeInTheDocument()
  })

  it("says card payments are not set up rather than reporting an error", () => {
    const unconfigured = new ApiError(
      "SERVICE_UNAVAILABLE",
      "Card payments are not configured.",
      undefined,
      503,
    )
    mockUsePatientCard.mockReturnValue({ data: null, isLoading: false, error: unconfigured })
    mockUsePatientCharges.mockReturnValue({ data: undefined, isLoading: false, error: unconfigured })

    render(<PaymentsTab patientId="patient-1" />)

    expect(
      screen.getByText("Card payments are not set up for this practice."),
    ).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /add a card/i })).not.toBeInTheDocument()
  })

  it("hides the add and replace actions in read-only mode", () => {
    vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")

    render(<PaymentsTab patientId="patient-1" />)

    // The record stays legible; only the affordance that opens a write flow goes.
    expect(screen.getByText("Visa •••• 4242")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /replace card/i })).not.toBeInTheDocument()
  })
})
