// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Billing page tests
 *
 * The setup checklist is guidance, not a gate: with nothing set up it appears
 * above the Claims tab and the tracker still renders under it, and the unbilled
 * queue is untouched.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import BillingPage from "../page"

vi.mock("@/components/billing/UnbilledQueue", () => ({
  UnbilledQueue: () => <div data-testid="unbilled-queue" />,
}))
vi.mock("@/components/billing/BillerExport", () => ({
  BillerExport: () => <div data-testid="biller-export" />,
}))
vi.mock("@/components/billing/claims/RemittanceHolds", () => ({
  RemittanceHolds: () => <div data-testid="remittance-holds" />,
}))
vi.mock("@/components/billing/claims/ClaimsTracker", () => ({
  ClaimsTracker: () => <div data-testid="claims-tracker" />,
}))

// A practice that has set nothing up, so every checklist step is outstanding.
vi.mock("@/hooks/useBillingProfile", () => ({
  useBillingProfile: () => ({ data: { legal_name: null } }),
}))
vi.mock("@/components/settings/useSettingsPreferences", () => ({
  useSettingsUserStatus: () => ({ data: { npi_number: null, taxonomy_code: null } }),
}))
vi.mock("@/hooks/useCoverage", () => ({ usePayers: () => ({ data: { data: [], total: 0 } }) }))
vi.mock("@/hooks/useBilling", () => ({ useUnbilledQueue: () => ({ data: { items: [] } }) }))
vi.mock("@/hooks/useClaims", () => ({ useClaims: () => ({ data: { data: [], total: 0 } }) }))

describe("BillingPage", () => {
  it("shows the checklist above the tracker without replacing it", async () => {
    const user = userEvent.setup()
    render(<BillingPage />)

    await user.click(screen.getByTestId("billing-tab-claims"))

    expect(screen.getByTestId("claims-setup-checklist")).toBeInTheDocument()
    expect(screen.getByTestId("claims-tracker")).toBeInTheDocument()
  })

  it("leaves the unbilled queue alone", () => {
    render(<BillingPage />)

    expect(screen.getByTestId("unbilled-queue")).toBeInTheDocument()
    expect(screen.getByTestId("biller-export")).toBeInTheDocument()
  })
})

describe("a client whose bill is held", () => {
  it("is surfaced without the therapist having to pick the right tab", () => {
    // The panel renders nothing when nothing is held, so placing it above
    // the tabs costs an empty node on almost every day — and on the day it
    // matters, a client has stopped being billed and nobody has to have
    // gone looking.
    render(<BillingPage />)

    expect(screen.getByTestId("remittance-holds")).toBeInTheDocument()
    expect(screen.queryByTestId("claims-tracker")).not.toBeInTheDocument()
  })
})

