// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The completeness banner and the gap list behind it: what claims still
 * need, named the way a claim review refuses.
 */

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import type { BillingProfileResponse } from "@/types/practiceBilling"

import { BillingProfileBanner } from "../BillingProfileBanner"
import { billingProfileGaps } from "../billingProfileGaps"

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

const clinician = { npi_number: "1234567893", taxonomy_code: "103T00000X" }

describe("billingProfileGaps", () => {
  it("names what a claim is refused without", () => {
    const gaps = billingProfileGaps(
      profile({ tax_id_last4: null, tax_id_type: null, city: null }),
      { npi_number: null, taxonomy_code: null },
    )

    expect(gaps.claims).toEqual(["tax id", "tax id type", "billing address", "your NPI"])
    expect(gaps.advisable).toEqual(["taxonomy code"])
  })

  it("keeps the clearinghouse's extra needs apart from the claim's", () => {
    const gaps = billingProfileGaps(profile({ billing_npi: null, contact_email: null }), clinician)

    expect(gaps.claims).toEqual([])
    expect(gaps.clearinghouse).toEqual(["billing NPI", "contact email"])
  })
})

describe("BillingProfileBanner", () => {
  it("lists what claims need", () => {
    const gaps = billingProfileGaps(profile({ tax_id_last4: null }), { ...clinician, npi_number: null })

    render(<BillingProfileBanner gaps={gaps} registered={false} />)

    expect(screen.getByTestId("billing-profile-banner")).toHaveTextContent(
      "Claims need: tax id, your NPI.",
    )
  })

  it("reads as done once registered", () => {
    render(<BillingProfileBanner gaps={billingProfileGaps(profile(), clinician)} registered />)

    expect(screen.getByTestId("billing-profile-banner")).toHaveTextContent(
      "Registered with your clearinghouse.",
    )
  })

  it("points at the clearinghouse's needs once claims are covered", () => {
    const gaps = billingProfileGaps(profile({ contact_email: null }), clinician)

    render(<BillingProfileBanner gaps={gaps} registered={false} />)

    expect(screen.getByTestId("billing-profile-banner")).toHaveTextContent(
      "Registering with your clearinghouse needs: contact email.",
    )
  })
})
