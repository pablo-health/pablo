// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Whether the chart asks for a card at all.
 *
 * The real hooks run here, with only the API functions mocked, because the
 * thing under test is the join: the user-status answer deciding whether the
 * card-on-file and charge-amount reads are ever sent. A practice that takes
 * no cards must make neither request and see no card UI; one that does must
 * see exactly what it saw before; and nothing card-shaped may flash up while
 * the answer is still on its way.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"

import { renderWithProviders } from "@/test/renderWithProviders"
import * as paymentsApi from "@/lib/api/payments"
import * as usersApi from "@/lib/api/users"
import type { UserStatus } from "@/lib/api/users"
import { BalanceTab } from "../BalanceTab"
import { ChargeCardSection } from "../ChargeCardSection"
import { PaymentsTab } from "../PaymentsTab"

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { uid: "u1" }, loading: false, getIdToken: async () => "token" }),
}))
vi.mock("@/lib/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/users")>()),
  getUserStatus: vi.fn(),
}))
vi.mock("@/lib/api/payments", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/payments")>()),
  listCharges: vi.fn(),
  fetchCardOnFile: vi.fn(),
  fetchChargeAmount: vi.fn(),
  fetchPatientBalance: vi.fn(),
}))
vi.mock("../AddCardDialog", () => ({
  AddCardDialog: () => null,
}))

function status(cardPaymentsConfigured: boolean): UserStatus {
  // Only the field under test is read; the rest of the shape is irrelevant here.
  return { card_payments_configured: cardPaymentsConfigured } as UserStatus
}

function renderChart() {
  return renderWithProviders(
    <>
      <BalanceTab patientId="patient-1" />
      <section data-testid="payments-tab">
        <PaymentsTab patientId="patient-1" />
      </section>
      <ChargeCardSection patientId="patient-1" />
    </>,
  )
}

describe("the card payments gate", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(paymentsApi.listCharges).mockResolvedValue([])
    vi.mocked(paymentsApi.fetchCardOnFile).mockResolvedValue(null)
    vi.mocked(paymentsApi.fetchChargeAmount).mockResolvedValue({
      amount_cents: 15000,
      currency: "usd",
    })
    vi.mocked(paymentsApi.fetchPatientBalance).mockResolvedValue({
      owed_cents: 15000,
      collected_cents: 0,
      written_off_cents: 0,
      adjusted_cents: 0,
      credited_cents: 0,
      balance_cents: 15000,
      outcome_known: true,
      by_visit: [],
    })
  })

  it("never asks for a card or an amount in a practice that takes no cards", async () => {
    vi.mocked(usersApi.getUserStatus).mockResolvedValue(status(false))

    renderChart()

    expect(
      await screen.findByText("Card payments are not set up for this practice."),
    ).toBeInTheDocument()
    // The balance still totals without a card processor.
    expect(await screen.findByText(/\$150\.00/)).toBeInTheDocument()
    expect(paymentsApi.fetchCardOnFile).not.toHaveBeenCalled()
    expect(paymentsApi.fetchChargeAmount).not.toHaveBeenCalled()
    expect(screen.queryByRole("button", { name: /add a card/i })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /charge/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/no card on file/i)).not.toBeInTheDocument()
    // The balance keeps its own ledger; under "not set up", the Payments tab's
    // "No charges yet." would read as if charging were possible.
    expect(
      within(screen.getByTestId("payments-tab")).queryByText("No charges yet."),
    ).not.toBeInTheDocument()
  })

  it("reads the card and the amount, as before, in a practice that takes cards", async () => {
    vi.mocked(usersApi.getUserStatus).mockResolvedValue(status(true))

    renderChart()

    await waitFor(() => {
      expect(paymentsApi.fetchCardOnFile).toHaveBeenCalledWith("patient-1", undefined)
      expect(paymentsApi.fetchChargeAmount).toHaveBeenCalledWith(
        "patient-1",
        undefined,
        undefined,
      )
    })
    expect(
      await screen.findAllByRole("button", { name: /add a card/i }),
    ).not.toHaveLength(0)
    expect(
      within(screen.getByTestId("payments-tab")).getByText("No charges yet."),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("Card payments are not set up for this practice."),
    ).not.toBeInTheDocument()
  })

  it("shows no card UI while it is still unknown whether cards are taken", () => {
    vi.mocked(usersApi.getUserStatus).mockReturnValue(new Promise<UserStatus>(() => {}))

    renderChart()

    expect(paymentsApi.fetchCardOnFile).not.toHaveBeenCalled()
    expect(paymentsApi.fetchChargeAmount).not.toHaveBeenCalled()
    expect(screen.queryByRole("button", { name: /add a card/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/no card on file/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/not set up/i)).not.toBeInTheDocument()
  })
})
