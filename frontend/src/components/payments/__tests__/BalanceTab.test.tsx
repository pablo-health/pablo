// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * BalanceTab tests — the chart's "what do they owe, and why" surface.
 *
 * Covers the states a practice actually meets: a balance with the rows
 * behind it grouped by visit, a settled account, a credit the practice owes,
 * the charge action appearing only when there is something to charge and a
 * card to charge it to, a decline that keeps its reason, and a deployment
 * with no card processing at all — where the balance still totals, because
 * the money is owed either way.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { BalanceTab } from "../BalanceTab"
import { ApiError } from "@/lib/api/client"
import type {
  BalanceResponse,
  CardOnFileResponse,
  ChargeResponse,
  VisitBalanceResponse,
} from "@/types/payments"

const mockUsePatientBalance = vi.fn()
const mockUsePatientCharges = vi.fn()
const mockUsePatientCard = vi.fn()
const mockChargeBalance = vi.fn()
const mockFetchStatement = vi.fn()

vi.mock("@/hooks/usePayments", () => ({
  usePatientBalance: (...args: unknown[]) => mockUsePatientBalance(...args),
  usePatientCharges: (...args: unknown[]) => mockUsePatientCharges(...args),
  usePatientCard: (...args: unknown[]) => mockUsePatientCard(...args),
  useChargeBalance: () => ({
    mutateAsync: mockChargeBalance,
    isPending: false,
  }),
}))

vi.mock("@/lib/access/readOnlyMode", () => ({
  useReadOnlyMode: () => ({ readOnly: false }),
}))

vi.mock("@/lib/api/payments", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/payments")>()
  return { ...actual, fetchStatement: (...args: unknown[]) => mockFetchStatement(...args) }
})

const VISIT = "visit-1"

const CARD: CardOnFileResponse = {
  brand: "visa",
  last4: "4242",
  exp_month: 4,
  exp_year: 2030,
  chargeable: true,
}

function visit(overrides: Partial<VisitBalanceResponse> = {}): VisitBalanceResponse {
  return {
    appointment_id: VISIT,
    owed_cents: 0,
    collected_cents: 0,
    written_off_cents: 0,
    adjusted_cents: 0,
    credited_cents: 0,
    balance_cents: 0,
    ...overrides,
  }
}

function balance(overrides: Partial<BalanceResponse> = {}): BalanceResponse {
  return {
    owed_cents: 0,
    collected_cents: 0,
    written_off_cents: 0,
    adjusted_cents: 0,
    credited_cents: 0,
    balance_cents: 0,
    by_visit: [],
    ...overrides,
  }
}

function charge(overrides: Partial<ChargeResponse> = {}): ChargeResponse {
  return {
    id: "charge-1",
    amount_cents: 6200,
    currency: "usd",
    status: "pending",
    status_detail: null,
    appointment_id: VISIT,
    kind: "patient_resp",
    claim_id: null,
    write_off_reason: null,
    note: null,
    settled_by_charge_id: null,
    created_at: "2026-05-24T10:00:00Z",
    updated_at: null,
    ...overrides,
  }
}

function setup({
  balanceData = balance(),
  charges = [] as ChargeResponse[],
  card = CARD as CardOnFileResponse | null,
  cardError = null as unknown,
} = {}) {
  mockUsePatientBalance.mockReturnValue({ data: balanceData, isLoading: false, error: null })
  mockUsePatientCharges.mockReturnValue({ data: charges, isLoading: false, error: null })
  mockUsePatientCard.mockReturnValue({ data: card, isLoading: false, error: cardError })
  render(<BalanceTab patientId="patient-1" />)
}

describe("BalanceTab", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe("the total", () => {
    it("says what the client owes", () => {
      setup({ balanceData: balance({ owed_cents: 6200, balance_cents: 6200 }) })

      expect(screen.getByTestId("balance-total")).toHaveTextContent("Owes $62.00")
    })

    it("says Credit when the practice owes the client", () => {
      setup({ balanceData: balance({ credited_cents: 1000, balance_cents: -1000 }) })

      expect(screen.getByTestId("balance-total")).toHaveTextContent("Credit $10.00")
    })

    it("says nothing is owed on a settled account", () => {
      setup()

      expect(screen.getByTestId("balance-total")).toHaveTextContent("Nothing owed")
    })
  })

  describe("the rows behind it", () => {
    it("names what each row is and links a claim-written row to its claim", () => {
      setup({
        balanceData: balance({
          owed_cents: 6200,
          balance_cents: 6200,
          by_visit: [visit({ owed_cents: 6200, balance_cents: 6200 })],
        }),
        charges: [charge({ claim_id: "claim-1" })],
      })

      expect(screen.getByText("Client responsibility")).toBeInTheDocument()
      expect(screen.getByRole("link", { name: "View claim" })).toHaveAttribute(
        "href",
        "/dashboard/billing/claims/claim-1",
      )
    })

    it("collects the rows that belong to no visit under one heading", () => {
      setup({
        balanceData: balance({
          owed_cents: 5000,
          balance_cents: 5000,
          by_visit: [visit({ appointment_id: null, owed_cents: 5000, balance_cents: 5000 })],
        }),
        charges: [charge({ appointment_id: null, kind: "session" })],
      })

      expect(screen.getByText("Other charges")).toBeInTheDocument()
    })

    it("keeps a declined row and its reason", () => {
      setup({
        balanceData: balance({
          owed_cents: 6200,
          balance_cents: 6200,
          by_visit: [visit({ owed_cents: 6200, balance_cents: 6200 })],
        }),
        charges: [
          charge({ kind: "session", status: "failed", status_detail: "insufficient_funds" }),
        ],
      })

      expect(screen.getByText("The card has insufficient funds.")).toBeInTheDocument()
    })

    it("says so when there are no charges at all", () => {
      setup()

      expect(screen.getByText("No charges yet.")).toBeInTheDocument()
    })
  })

  describe("charging the balance", () => {
    it("offers the action with the amount on it", () => {
      setup({ balanceData: balance({ owed_cents: 6200, balance_cents: 6200 }) })

      expect(
        screen.getByRole("button", { name: /Charge balance \(\$62\.00\)/ }),
      ).toBeEnabled()
    })

    it("is not offered when nothing is owed", () => {
      setup()

      expect(screen.queryByRole("button", { name: /Charge balance/ })).not.toBeInTheDocument()
    })

    it("is not offered on a credit balance", () => {
      setup({ balanceData: balance({ credited_cents: 1000, balance_cents: -1000 }) })

      expect(screen.queryByRole("button", { name: /Charge balance/ })).not.toBeInTheDocument()
    })

    it("says why it cannot charge when there is no card on file", () => {
      setup({ balanceData: balance({ owed_cents: 6200, balance_cents: 6200 }), card: null })

      expect(screen.getByRole("button", { name: /Charge balance/ })).toBeDisabled()
      expect(screen.getByText(/No card on file for this client/)).toBeInTheDocument()
    })

    it("charges without sending an amount, so the server reads the current one", async () => {
      mockChargeBalance.mockResolvedValue(charge({ status: "succeeded", kind: "payment" }))
      setup({ balanceData: balance({ owed_cents: 6200, balance_cents: 6200 }) })

      await userEvent.click(screen.getByRole("button", { name: /Charge balance/ }))

      expect(mockChargeBalance).toHaveBeenCalledWith({ patientId: "patient-1" })
      expect(await screen.findByText(/Charged \$62\.00/)).toBeInTheDocument()
    })

    it("shows the processor's reason on a decline", async () => {
      mockChargeBalance.mockResolvedValue(
        charge({ status: "failed", status_detail: "expired_card", kind: "payment" }),
      )
      setup({ balanceData: balance({ owed_cents: 6200, balance_cents: 6200 }) })

      await userEvent.click(screen.getByRole("button", { name: /Charge balance/ }))

      expect(await screen.findByRole("alert")).toHaveTextContent("The card has expired.")
    })

    it("points at the ledger when the attempt never completed", async () => {
      mockChargeBalance.mockRejectedValue(new Error("network"))
      setup({ balanceData: balance({ owed_cents: 6200, balance_cents: 6200 }) })

      await userEvent.click(screen.getByRole("button", { name: /Charge balance/ }))

      expect(await screen.findByRole("alert")).toHaveTextContent(
        /Check this client's charges before retrying/,
      )
    })
  })

  describe("a deployment that takes no cards", () => {
    it("still totals the balance and simply drops the charge action", () => {
      setup({
        balanceData: balance({ owed_cents: 4000, balance_cents: 4000 }),
        card: null,
        cardError: new ApiError("UNAVAILABLE", "not configured", undefined, 503),
      })

      expect(screen.getByTestId("balance-total")).toHaveTextContent("Owes $40.00")
      expect(screen.queryByRole("button", { name: /Charge balance/ })).not.toBeInTheDocument()
      expect(screen.queryByText(/No card on file/)).not.toBeInTheDocument()
    })
  })

  describe("the statement", () => {
    it("is offered whether or not anything is owed", () => {
      setup()

      expect(screen.getByRole("button", { name: /Statement/ })).toBeEnabled()
    })

    it("says so when the document could not be produced", async () => {
      mockFetchStatement.mockRejectedValue(new Error("boom"))
      setup({ balanceData: balance({ owed_cents: 6200, balance_cents: 6200 }) })

      await userEvent.click(screen.getByRole("button", { name: /Statement/ }))

      await waitFor(() =>
        expect(screen.getByRole("alert")).toHaveTextContent(
          "The statement could not be generated.",
        ),
      )
    })
  })
})
