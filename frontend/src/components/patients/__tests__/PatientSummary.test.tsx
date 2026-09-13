// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientSummary tests — the badges in the chart header.
 *
 * Both are things a clinician needs BEFORE the session rather than after
 * hunting for them on a tab, so both live beside the name.
 *
 * The eligibility badge is present when a plan is on file, absent when there
 * is none (no plan is not a coverage status), and never worded as a payment
 * guarantee. The balance line says what the client owes, says "Credit" rather
 * than a negative number when the practice owes them, and says nothing at all
 * when the account is settled — which is the ordinary case, and "Owes $0.00"
 * beside every name is noise that trains people to stop reading the line that
 * matters.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

import { PatientSummary } from "../PatientSummary"
import type { PatientResponse } from "@/types/patients"

const mockUsePatientCoverage = vi.fn()
const mockUsePatientBalance = vi.fn()

vi.mock("@/hooks/useCoverage", () => ({
  usePatientCoverage: (...args: unknown[]) => mockUsePatientCoverage(...args),
}))

vi.mock("@/hooks/usePayments", () => ({
  usePatientBalance: (...args: unknown[]) => mockUsePatientBalance(...args),
}))

function balanceOf(balanceCents: number, outcomeKnown = true) {
  return {
    data: {
      owed_cents: Math.max(balanceCents, 0),
      collected_cents: 0,
      written_off_cents: 0,
      adjusted_cents: 0,
      credited_cents: Math.max(-balanceCents, 0),
      balance_cents: balanceCents,
      outcome_known: outcomeKnown,
      by_visit: [],
    },
  }
}

const PATIENT = {
  id: "patient-1",
  first_name: "Jane",
  last_name: "Roe",
  email: null,
  phone: null,
  date_of_birth: null,
  diagnosis: null,
  status: "active",
  session_count: 0,
  last_session_date: null,
  next_session_date: null,
} as unknown as PatientResponse // the header reads only these fields

describe("PatientSummary", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUsePatientBalance.mockReturnValue({ data: undefined })
  })

  it("shows the plan's last answer beside the name", () => {
    mockUsePatientCoverage.mockReturnValue({
      data: {
        eligibility: {
          status: "active",
          checked_at: "2026-09-06T15:00:00Z",
          carveout_administrator: null,
          aaa_errors: [],
        },
      },
    })

    render(<PatientSummary patient={PATIENT} />)

    const badge = screen.getByTestId("eligibility-badge")
    expect(badge).toHaveTextContent(/Plan active as of/)
    expect(badge.textContent?.toLowerCase()).not.toContain("covered")
  })

  it("shows a not-yet-checked badge for a plan with no answer", () => {
    mockUsePatientCoverage.mockReturnValue({ data: { eligibility: null } })

    render(<PatientSummary patient={PATIENT} />)

    expect(screen.getByTestId("eligibility-badge")).toHaveTextContent("Plan not yet checked")
  })

  it("shows nothing when there is no plan on file", () => {
    mockUsePatientCoverage.mockReturnValue({ data: null })

    render(<PatientSummary patient={PATIENT} />)

    expect(screen.queryByTestId("eligibility-badge")).not.toBeInTheDocument()
  })

  describe("the balance line", () => {
    beforeEach(() => {
      mockUsePatientCoverage.mockReturnValue({ data: null })
    })

    it("says what the client owes", () => {
      mockUsePatientBalance.mockReturnValue(balanceOf(6200))

      render(<PatientSummary patient={PATIENT} />)

      expect(screen.getByTestId("chart-balance")).toHaveTextContent("Owes $62.00")
    })

    it("says Credit rather than a negative number when the practice owes", () => {
      mockUsePatientBalance.mockReturnValue(balanceOf(-1000))

      render(<PatientSummary patient={PATIENT} />)

      const badge = screen.getByTestId("chart-balance")
      expect(badge).toHaveTextContent("Credit $10.00")
      expect(badge).not.toHaveTextContent("-$")
    })

    it("shows no line at all when the account is settled", () => {
      mockUsePatientBalance.mockReturnValue(balanceOf(0))

      render(<PatientSummary patient={PATIENT} />)

      expect(screen.queryByTestId("chart-balance")).not.toBeInTheDocument()
    })

    it("shows no line while the balance is still loading", () => {
      mockUsePatientBalance.mockReturnValue({ data: undefined })

      render(<PatientSummary patient={PATIENT} />)

      expect(screen.queryByTestId("chart-balance")).not.toBeInTheDocument()
    })
  })

  describe("when the payer settles somewhere else", () => {
    /**
     * This client's plan pays a billing service, so their share of a visit
     * never becomes a row here. The total is a floor, and the header has to
     * say which of the two it is showing.
     */
    it("says a figure is only the least they owe", () => {
      mockUsePatientBalance.mockReturnValue(balanceOf(6200, false))

      render(<PatientSummary patient={PATIENT} />)

      expect(screen.getByTestId("chart-balance")).toHaveTextContent(
        "Owes at least $62.00",
      )
    })

    it("breaks its own silence rule at zero", () => {
      // Everywhere else a zero balance shows nothing, because a settled
      // client is the ordinary case. Here zero means we were never told —
      // and silence would be indistinguishable from settled.
      mockUsePatientBalance.mockReturnValue(balanceOf(0, false))

      render(<PatientSummary patient={PATIENT} />)

      expect(screen.getByTestId("chart-balance")).toHaveTextContent(
        "Balance tracked elsewhere",
      )
    })

    it("still states a credit plainly", () => {
      // Money the practice took is money it took; no missing remittance
      // makes a refund it owes any less owed.
      mockUsePatientBalance.mockReturnValue(balanceOf(-1000, false))

      render(<PatientSummary patient={PATIENT} />)

      expect(screen.getByTestId("chart-balance")).toHaveTextContent("Credit $10.00")
    })
  })
})
