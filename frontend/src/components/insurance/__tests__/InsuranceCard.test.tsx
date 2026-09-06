// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * InsuranceCard tests — the chart's coverage on file.
 *
 * The states a practice meets: nothing on file (offer to add), a plan on
 * file rendered from what is on the card, a subscriber who is not the
 * client, and what the last eligibility check found — active with its
 * figures, a behavioral carve-out, a payer refusal with the vendor's
 * resolution text. The coverage hooks are mocked so each state can be
 * driven directly and `CoverageDialog` is stubbed — the form has its own
 * concerns.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { InsuranceCard } from "../InsuranceCard"
import type { CoverageResponse, EligibilitySummary, PayerResponse } from "@/types/coverage"

const mockUsePatientCoverage = vi.fn()
const mockDeactivate = vi.fn()
const mockVerify = vi.fn()

vi.mock("@/hooks/useCoverage", () => ({
  usePatientCoverage: (...args: unknown[]) => mockUsePatientCoverage(...args),
  useDeactivateCoverage: () => ({ mutate: mockDeactivate, isPending: false }),
  useVerifyCoverage: () => ({ mutate: mockVerify, isPending: false }),
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

function eligibility(overrides: Partial<EligibilitySummary> = {}): EligibilitySummary {
  return {
    status: "active",
    checked_at: "2026-09-06T15:00:00Z",
    payer_name: "AETNA",
    plan_name: "Choice POS II",
    plan_begin: "2026-01-01",
    copay_cents: 2500,
    coinsurance_pct: 20,
    deductible_remaining_cents: 31250,
    visit_limit: { remaining: 12, total: 30 },
    requires_authorization: true,
    carveout_administrator: null,
    aaa_errors: [],
    ...overrides,
  }
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
    eligibility: null,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
    ...overrides,
  }
}

function onFile(overrides: Partial<CoverageResponse> = {}) {
  mockUsePatientCoverage.mockReturnValue({
    data: coverage(overrides),
    isLoading: false,
    error: null,
  })
}

describe("InsuranceCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders the plan on file from the card's details", () => {
    onFile()

    render(<InsuranceCard patientId="patient-1" />)

    expect(screen.getByText("Aetna")).toBeInTheDocument()
    expect(screen.getByText(/Payer ID 60054/)).toBeInTheDocument()
    expect(screen.getByText(/Plan not yet checked/)).toBeInTheDocument()
    expect(screen.getByText("W123456789")).toBeInTheDocument()
    expect(screen.getByText("GRP-77")).toBeInTheDocument()
    expect(screen.getByText("Choice POS II")).toBeInTheDocument()
    expect(screen.getByText("Self")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument()
    expect(screen.queryByTestId("eligibility-details")).not.toBeInTheDocument()
  })

  it("names the subscriber when it is somebody other than the client", () => {
    onFile({
      subscriber_relationship: "child",
      subscriber_first_name: "Parent",
      subscriber_last_name: "Person",
      subscriber_date_of_birth: "1980-02-03",
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
    onFile()
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

  it("re-verifies on demand", async () => {
    onFile()
    const user = userEvent.setup()

    render(<InsuranceCard patientId="patient-1" />)

    await user.click(screen.getByRole("button", { name: "Re-verify" }))
    expect(mockVerify).toHaveBeenCalledWith(
      { patientId: "patient-1" },
      expect.objectContaining({ onError: expect.any(Function) }),
    )
  })

  it("renders what an active check found, without promising payment", () => {
    onFile({ verified_at: "2026-09-06T15:00:00Z", eligibility: eligibility() })

    render(<InsuranceCard patientId="patient-1" />)

    expect(screen.getByText(/Plan active as of/)).toBeInTheDocument()
    const details = screen.getByTestId("eligibility-details")
    expect(details).toHaveTextContent("$25.00")
    expect(details).toHaveTextContent("20%")
    expect(details).toHaveTextContent("$312.50")
    expect(details).toHaveTextContent("12 of 30 remaining")
    expect(details).toHaveTextContent("Required")
    expect(details).toHaveTextContent("Not a payment guarantee")
    expect(details.textContent?.toLowerCase()).not.toContain("covered")
  })

  it("says where behavioral claims go on a carve-out", () => {
    onFile({
      verified_at: "2026-09-06T15:00:00Z",
      eligibility: eligibility({
        carveout_administrator: { name: "EXAMPLE BEHAVIORAL HEALTH", payer_id: "EXBH1" },
      }),
    })

    render(<InsuranceCard patientId="patient-1" />)

    expect(
      screen.getByText(
        "Behavioral benefits administered by EXAMPLE BEHAVIORAL HEALTH (payer ID EXBH1). File claims there.",
      ),
    ).toBeInTheDocument()
  })

  it("renders a payer refusal with the vendor's resolution text", () => {
    onFile({
      verified_at: "2026-09-06T15:00:00Z",
      eligibility: eligibility({
        status: "error",
        copay_cents: null,
        aaa_errors: [
          {
            code: "72",
            description: "Invalid/Missing Subscriber/Insured ID",
            followup_action: "Please Correct and Resubmit",
            resolution: "The subscriber's member ID is either missing or invalid.",
          },
        ],
      }),
    })

    render(<InsuranceCard patientId="patient-1" />)

    expect(screen.getByText(/Payer could not confirm the plan/)).toBeInTheDocument()
    expect(
      screen.getByText(/Invalid\/Missing Subscriber\/Insured ID \(72\) — Please Correct and Resubmit/),
    ).toBeInTheDocument()
    expect(
      screen.getByText("The subscriber's member ID is either missing or invalid."),
    ).toBeInTheDocument()
  })
})
