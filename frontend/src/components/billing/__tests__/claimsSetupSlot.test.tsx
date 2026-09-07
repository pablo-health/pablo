// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The claims-setup extra-steps slot.
 *
 * The base build renders nothing from it; a downstream build replaces the
 * extensions file and its rows land in the checklist's own list, beside the
 * core steps rather than in a card of their own.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { ClaimsSetupChecklist } from "../ClaimsSetupChecklist"

vi.mock("../billingSlots.extensions", () => ({
  ClaimsSetupSteps: () => <li data-testid="claims-setup-step-enrollment">Enroll with your payers</li>,
}))

vi.mock("@/hooks/useBillingProfile", () => ({
  useBillingProfile: () => ({ data: { legal_name: null } }),
}))
vi.mock("@/components/settings/useSettingsPreferences", () => ({
  useSettingsUserStatus: () => ({ data: { npi_number: null, taxonomy_code: null } }),
}))
vi.mock("@/hooks/useCoverage", () => ({ usePayers: () => ({ data: { data: [], total: 0 } }) }))
vi.mock("@/hooks/useBilling", () => ({ useUnbilledQueue: () => ({ data: { items: [] } }) }))
vi.mock("@/hooks/useClaims", () => ({ useClaims: () => ({ data: { data: [], total: 0 } }) }))

describe("claims setup extra-steps slot", () => {
  it("renders a downstream build's steps alongside the core ones", () => {
    render(<ClaimsSetupChecklist />)

    const list = screen.getByTestId("claims-setup-step-profile").parentElement
    expect(list).toContainElement(screen.getByTestId("claims-setup-step-enrollment"))
  })
})
