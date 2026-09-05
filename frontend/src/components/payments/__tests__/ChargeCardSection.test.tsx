// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChargeCardSection tests.
 *
 * The paths worth holding still are the ones that involve somebody's money:
 * a decline has to say why and offer a retry that is a fresh, explicit charge;
 * a client with no card must be offered one rather than shown a dead button;
 * and the figure on the button has to be the figure that gets sent. The
 * payment hooks are mocked so each state can be driven directly, and
 * `AddCardDialog` is stubbed because Stripe.js is not under test here.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { ChargeCardSection } from "../ChargeCardSection"
import { ApiError } from "@/lib/api/client"
import type { CardOnFileResponse, ChargeResponse } from "@/types/payments"

const mockUsePatientCard = vi.fn()
const mockUseChargeAmount = vi.fn()
const mockCharge = vi.fn()
const mockUseCreateCharge = vi.fn()

vi.mock("@/hooks/usePayments", () => ({
  usePatientCard: (...args: unknown[]) => mockUsePatientCard(...args),
  useChargeAmount: (...args: unknown[]) => mockUseChargeAmount(...args),
  useCreateCharge: (...args: unknown[]) => mockUseCreateCharge(...args),
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
    updated_at: "2026-05-24T10:00:01Z",
    ...overrides,
  }
}

describe("ChargeCardSection", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUsePatientCard.mockReturnValue({ data: CARD, isLoading: false, error: null })
    mockUseChargeAmount.mockReturnValue({
      data: { amount_cents: 15000, currency: "usd" },
      isLoading: false,
      error: null,
    })
    mockCharge.mockResolvedValue(charge())
    mockUseCreateCharge.mockReturnValue({ mutateAsync: mockCharge, isPending: false })
  })

  it("shows the card and the amount before anything is charged", () => {
    render(<ChargeCardSection patientId="patient-1" />)

    expect(screen.getByText("Visa •••• 4242")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Charge $150.00" })).toBeInTheDocument()
  })

  it("charges the resolved amount and reports what was charged", async () => {
    const user = userEvent.setup()
    render(<ChargeCardSection patientId="patient-1" />)

    await user.click(screen.getByRole("button", { name: "Charge $150.00" }))

    // No amount is sent: the backend resolves the same rate it previewed, and
    // echoing it back would let the two drift.
    expect(mockCharge).toHaveBeenCalledWith({ patientId: "patient-1", data: {} })
    expect(await screen.findByText(/Charged \$150\.00 to Visa •••• 4242\./)).toBeInTheDocument()
  })

  it("shows the processor's reason for a decline and offers an explicit retry", async () => {
    const user = userEvent.setup()
    mockCharge.mockResolvedValue(
      charge({ status: "failed", status_detail: "insufficient_funds" }),
    )
    render(<ChargeCardSection patientId="patient-1" />)

    await user.click(screen.getByRole("button", { name: "Charge $150.00" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The card has insufficient funds.",
    )
    // A retry is a new charge the clinician asks for — nothing re-fires on
    // its own.
    const retry = await screen.findByRole("button", { name: "Try again" })
    expect(mockCharge).toHaveBeenCalledTimes(1)

    mockCharge.mockResolvedValue(charge())
    await user.click(retry)

    await waitFor(() => expect(mockCharge).toHaveBeenCalledTimes(2))
    expect(await screen.findByText(/Charged \$150\.00/)).toBeInTheDocument()
  })

  it("renders an unrecognised decline code rather than inventing copy", async () => {
    const user = userEvent.setup()
    mockCharge.mockResolvedValue(
      charge({ status: "failed", status_detail: "some_new_code" }),
    )
    render(<ChargeCardSection patientId="patient-1" />)

    await user.click(screen.getByRole("button", { name: "Charge $150.00" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("some_new_code")
  })

  it("offers to add a card instead of dead-ending when there is none", () => {
    mockUsePatientCard.mockReturnValue({ data: null, isLoading: false, error: null })
    render(<ChargeCardSection patientId="patient-1" />)

    expect(screen.getByText("No card on file for this client.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /add a card/i })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^Charge/ })).not.toBeInTheDocument()
  })

  it("asks for an amount when no rate is set, and sends what was typed", async () => {
    const user = userEvent.setup()
    mockUseChargeAmount.mockReturnValue({
      data: { amount_cents: null, currency: "usd" },
      isLoading: false,
      error: null,
    })
    render(<ChargeCardSection patientId="patient-1" />)

    const button = screen.getByRole("button", { name: "Charge card" })
    expect(button).toBeDisabled()

    await user.type(screen.getByLabelText("Amount"), "160.10")
    await user.click(screen.getByRole("button", { name: "Charge $160.10" }))

    expect(mockCharge).toHaveBeenCalledWith({
      patientId: "patient-1",
      data: { amount_cents: 16010 },
    })
  })

  it("renders nothing when the practice has no card processing configured", () => {
    const unconfigured = new ApiError(
      "SERVICE_UNAVAILABLE",
      "Card payments are not configured.",
      undefined,
      503,
    )
    mockUsePatientCard.mockReturnValue({ data: null, isLoading: false, error: unconfigured })

    const { container } = render(<ChargeCardSection patientId="patient-1" />)

    expect(container).toBeEmptyDOMElement()
  })
})
