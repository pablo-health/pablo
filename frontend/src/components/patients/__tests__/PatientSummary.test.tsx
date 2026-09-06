// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientSummary tests — the eligibility badge in the chart header.
 *
 * The badge is seen before the first session, so it lives beside the name:
 * present when a plan is on file, absent when there is none (no plan is not
 * a coverage status), and never worded as a payment guarantee.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

import { PatientSummary } from "../PatientSummary"
import type { PatientResponse } from "@/types/patients"

const mockUsePatientCoverage = vi.fn()

vi.mock("@/hooks/useCoverage", () => ({
  usePatientCoverage: (...args: unknown[]) => mockUsePatientCoverage(...args),
}))

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
})
