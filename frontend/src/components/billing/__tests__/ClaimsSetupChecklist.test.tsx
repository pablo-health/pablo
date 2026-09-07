// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ClaimsSetupChecklist tests
 *
 * Covers each step's done/not-done state and where it links, the two ways the
 * coverage step is satisfied, and the card disappearing once setup is finished.
 * The extra-steps slot has its own file, which replaces it.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import { ClaimsSetupChecklist } from "../ClaimsSetupChecklist"
import type { UnbilledSessionItem } from "@/types/billing"
import type { BillingProfileResponse } from "@/types/practiceBilling"

const useBillingProfile = vi.hoisted(() => vi.fn())
const useSettingsUserStatus = vi.hoisted(() => vi.fn())
const usePayers = vi.hoisted(() => vi.fn())
const useUnbilledQueue = vi.hoisted(() => vi.fn())
const useClaims = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useBillingProfile", () => ({ useBillingProfile }))
vi.mock("@/components/settings/useSettingsPreferences", () => ({ useSettingsUserStatus }))
vi.mock("@/hooks/useCoverage", () => ({ usePayers }))
vi.mock("@/hooks/useBilling", () => ({ useUnbilledQueue }))
vi.mock("@/hooks/useClaims", () => ({ useClaims }))

function profile(overrides: Partial<BillingProfileResponse> = {}): BillingProfileResponse {
  return {
    legal_name: "Acme Therapy LLC",
    tax_id_last4: "9714",
    tax_id_type: "ein",
    billing_npi: "1999999984",
    address_line1: "1 Test St",
    address_line2: null,
    city: "Atlanta",
    state: "GA",
    postal_code: "30301",
    phone: "4045550100",
    contact_email: "billing@example.com",
    clearinghouse_provider_id: null,
    eligibility_auto_check: true,
    ...overrides,
  }
}

function unbilled(overrides: Partial<UnbilledSessionItem> = {}): UnbilledSessionItem {
  return {
    session_id: "sess-1",
    patient_id: "patient-1",
    patient_name: "Ada Early",
    session_date: "2026-06-10T14:00:00Z",
    amount_cents: 15000,
    currency: "usd",
    appointment_id: "appt-1",
    has_coverage: false,
    copay_cents: null,
    claim: null,
    ...overrides,
  }
}

interface SetupState {
  profile?: Partial<BillingProfileResponse>
  npi?: string | null
  payers?: number
  items?: UnbilledSessionItem[]
  claims?: number
}

/** A practice with everything set up, minus whatever the case takes away. */
function setupChecklist({
  profile: patch,
  npi = "1234567893",
  payers = 1,
  items = [],
  claims = 0,
}: SetupState = {}) {
  useBillingProfile.mockReturnValue({ data: profile(patch) })
  useSettingsUserStatus.mockReturnValue({
    data: { npi_number: npi, taxonomy_code: "103T00000X" },
  })
  usePayers.mockReturnValue({ data: { data: [], total: payers } })
  useUnbilledQueue.mockReturnValue({ data: { items } })
  useClaims.mockReturnValue({ data: { data: [], total: claims } })
}

function step(id: string) {
  return screen.getByTestId(`claims-setup-step-${id}`)
}

describe("ClaimsSetupChecklist", () => {
  it("shows every step still to do, each linking to the page that does it", () => {
    setupChecklist({ profile: { legal_name: null }, npi: null, payers: 0 })
    render(<ClaimsSetupChecklist />)

    expect(within(step("profile")).getByText("Not done:")).toBeInTheDocument()
    expect(within(step("payers")).getByText("Not done:")).toBeInTheDocument()
    expect(within(step("coverage")).getByText("Not done:")).toBeInTheDocument()

    expect(within(step("profile")).getByRole("link")).toHaveAttribute(
      "href",
      "/dashboard/settings/billing-profile",
    )
    expect(within(step("payers")).getByRole("link")).toHaveAttribute(
      "href",
      "/dashboard/settings/insurance",
    )
    expect(within(step("coverage")).getByRole("link")).toHaveAttribute(
      "href",
      "/dashboard/patients",
    )
  })

  it("names the profile fields a claim is refused without", () => {
    setupChecklist({ profile: { legal_name: null, phone: null }, payers: 0 })
    render(<ClaimsSetupChecklist />)

    expect(
      within(step("profile")).getByText("Claims still need legal name, phone."),
    ).toBeInTheDocument()
  })

  it("marks the profile and payer steps done once they are", () => {
    setupChecklist({ payers: 2 })
    render(<ClaimsSetupChecklist />)

    expect(within(step("profile")).getByText("Done:")).toBeInTheDocument()
    expect(within(step("payers")).getByText("Done:")).toBeInTheDocument()
    expect(within(step("coverage")).getByText("Not done:")).toBeInTheDocument()
  })

  it("counts coverage done when an unbilled client has a plan on file", () => {
    setupChecklist({ payers: 0, items: [unbilled({ has_coverage: true })] })
    render(<ClaimsSetupChecklist />)

    expect(within(step("coverage")).getByText("Done:")).toBeInTheDocument()
  })

  it("counts coverage done when a claim has already been filed", () => {
    setupChecklist({ payers: 0, claims: 3 })
    render(<ClaimsSetupChecklist />)

    expect(within(step("coverage")).getByText("Done:")).toBeInTheDocument()
  })

  it("points the coverage step at the client whose plan is missing", () => {
    setupChecklist({ payers: 0, items: [unbilled({ patient_id: "patient-7" })] })
    render(<ClaimsSetupChecklist />)

    expect(within(step("coverage")).getByRole("link")).toHaveAttribute(
      "href",
      "/dashboard/patients/patient-7",
    )
  })

  it("disappears once every step is done", () => {
    setupChecklist({ claims: 1 })
    render(<ClaimsSetupChecklist />)

    expect(screen.queryByTestId("claims-setup-checklist")).not.toBeInTheDocument()
  })

  it("renders nothing until every read is in", () => {
    setupChecklist({ payers: 0 })
    useBillingProfile.mockReturnValue({ data: undefined })
    render(<ClaimsSetupChecklist />)

    expect(screen.queryByTestId("claims-setup-checklist")).not.toBeInTheDocument()
  })
})
