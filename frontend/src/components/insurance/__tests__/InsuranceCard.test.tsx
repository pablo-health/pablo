// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * InsuranceCard tests — the chart's coverage on file.
 *
 * The states a practice meets: nothing on file (offer to add), a plan on
 * file rendered from what is on the card, and a subscriber who is not the
 * client. The coverage hooks are mocked so each state can be driven directly
 * and `CoverageDialog` is stubbed — the form has its own concerns.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { InsuranceCard } from "../InsuranceCard"
import type { CoverageResponse, PayerResponse } from "@/types/coverage"

const mockUsePatientCoverage = vi.fn()
const mockDeactivate = vi.fn()

vi.mock("@/hooks/useCoverage", () => ({
  usePatientCoverage: (...args: unknown[]) => mockUsePatientCoverage(...args),
  useDeactivateCoverage: () => ({ mutate: mockDeactivate, isPending: false }),
}))

vi.mock("../CoverageDialog", () => ({
  CoverageDialog: ({ open }: { open: boolean }) => (open ? <div>coverage-dialog</div> : null),
}))

const PAYER: PayerResponse = {
  id: "payer-1",
  name: "Aetna",
  payer_id: "60054",
  clearinghouse_payer_id: null,
  is_carveout: false,
  carveout_of: null,
  enrollment_status: "none",
  timely_filing_days: 90,
  corrected_claim_days: 90,
  appeal_days: 180,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
}

function coverage(overrides: Partial<CoverageResponse> = {}): CoverageResponse {
  return {
    id: "cov-1",
    patient_id: "patient-1",
    payer: PAYER,
    member_id: "W123456789",
    group_number: "GRP-77",
    plan_name: "Choice POS II",
    subscriber_relationship: "self",
    subscriber_first_name: null,
    subscriber_last_name: null,
    subscriber_date_of_birth: null,
    subscriber_sex: null,
    subscriber_address_line1: null,
    subscriber_address_line2: null,
    subscriber_city: null,
    subscriber_state: null,
    subscriber_postal_code: null,
    active: true,
    verified_at: null,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
    ...overrides,
  }
}

describe("InsuranceCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders the plan on file from the card's details", () => {
    mockUsePatientCoverage.mockReturnValue({ data: coverage(), isLoading: false, error: null })

    render(<InsuranceCard patientId="patient-1" />)

    expect(screen.getByText("Aetna")).toBeInTheDocument()
    expect(screen.getByText(/Payer ID 60054/)).toBeInTheDocument()
    expect(screen.getByText(/Not yet verified/)).toBeInTheDocument()
    expect(screen.getByText("W123456789")).toBeInTheDocument()
    expect(screen.getByText("GRP-77")).toBeInTheDocument()
    expect(screen.getByText("Choice POS II")).toBeInTheDocument()
    expect(screen.getByText("Self")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument()
  })

  it("names the subscriber when it is somebody other than the client", () => {
    mockUsePatientCoverage.mockReturnValue({
      data: coverage({
        subscriber_relationship: "child",
        subscriber_first_name: "Parent",
        subscriber_last_name: "Person",
        subscriber_date_of_birth: "1980-02-03",
      }),
      isLoading: false,
      error: null,
    })

    render(<InsuranceCard patientId="patient-1" />)

    expect(screen.getByText("Child — Parent Person")).toBeInTheDocument()
    expect(screen.getByText("1980-02-03")).toBeInTheDocument()
  })

  it("offers to add coverage when there is none", async () => {
    mockUsePatientCoverage.mockReturnValue({ data: null, isLoading: false, error: null })
    const user = userEvent.setup()

    render(<InsuranceCard patientId="patient-1" />)

    expect(screen.getByText("No insurance on file for this client.")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Add coverage" }))
    expect(screen.getByText("coverage-dialog")).toBeInTheDocument()
  })

  it("asks before removing, then deactivates", async () => {
    mockUsePatientCoverage.mockReturnValue({ data: coverage(), isLoading: false, error: null })
    const user = userEvent.setup()

    render(<InsuranceCard patientId="patient-1" />)

    await user.click(screen.getByRole("button", { name: "Remove" }))
    expect(mockDeactivate).not.toHaveBeenCalled()
    await user.click(screen.getByRole("button", { name: "Remove coverage" }))
    expect(mockDeactivate).toHaveBeenCalledWith(
      { patientId: "patient-1" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })
})
