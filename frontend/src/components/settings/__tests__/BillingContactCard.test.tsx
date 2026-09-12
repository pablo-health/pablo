// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Billing contact — where insurers and the clearinghouse reach the practice.
 *
 * It saves ONLY its own fields, so a therapist can fill in her contact details
 * without touching the identity card beside it, and neither can blank the
 * other's work by omitting it from a patch.
 */

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { BillingProfileResponse } from "@/types/practiceBilling"
import { BillingContactCard } from "../BillingContactCard"

const mockUpdate = vi.fn()

vi.mock("@/hooks/useBillingProfile", () => ({
  useUpdateBillingProfile: () => ({ mutate: mockUpdate, isPending: false }),
}))

vi.mock("../SettingsSavedContext", () => ({
  useSettingsSaved: () => ({ flashSaved: vi.fn() }),
}))

function profile(overrides: Partial<BillingProfileResponse> = {}): BillingProfileResponse {
  return {
    legal_name: "Acme Therapy LLC",
    tax_id_last4: "9714",
    tax_id_type: "ein",
    billing_npi: "1999999984",
    address_line1: null,
    address_line2: null,
    city: null,
    state: null,
    postal_code: null,
    phone: null,
    contact_email: null,
    clearinghouse_provider_id: null,
    eligibility_auto_check: true,
    allow_courtesy_writeoffs: false,
    small_balance_cents: 500,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("BillingContactCard", () => {
  it("sends only the contact fields, never the identity ones", async () => {
    // The endpoint merges what it is given, so sending a field this card does
    // not own would overwrite whatever the identity card had just saved.
    const user = userEvent.setup()
    render(<BillingContactCard profile={profile()} />)

    await user.type(screen.getByLabelText("City"), "Atlanta")
    await user.click(screen.getByRole("button", { name: "Save" }))

    const [patch] = mockUpdate.mock.calls[0]
    expect(patch).toEqual({ city: "Atlanta" })
    expect(patch).not.toHaveProperty("legal_name")
    expect(patch).not.toHaveProperty("billing_npi")
  })

  it("refuses a state that is not two letters", async () => {
    const user = userEvent.setup()
    render(<BillingContactCard profile={profile()} />)

    await user.type(screen.getByLabelText("State"), "G")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(screen.getByRole("alert")).toHaveTextContent("two-letter")
    expect(mockUpdate).not.toHaveBeenCalled()
  })

  it("asks for an address she reads, not a general practice inbox", async () => {
    // The old wording told a solo therapist to use "the practice's general
    // address, not a clinician's own" — advice for a group practice, and
    // nonsense for someone working alone, who IS the practice.
    render(<BillingContactCard profile={profile()} />)

    expect(screen.getByText(/address you check regularly/i)).toBeInTheDocument()
    expect(screen.queryByText(/not a clinician/i)).not.toBeInTheDocument()
  })

  it("offers the practice details it already knows, without saving them", async () => {
    const user = userEvent.setup()
    render(
      <BillingContactCard
        profile={profile()}
        practiceDetails={{ phone: "4045550100", address: "1 Test St" }}
      />,
    )

    await user.click(screen.getByRole("button", { name: /use my practice details/i }))

    expect(screen.getByLabelText("Phone")).toHaveValue("4045550100")
    expect(screen.getByLabelText("Billing address")).toHaveValue("1 Test St")
    expect(mockUpdate).not.toHaveBeenCalled()
  })

  it("offers nothing to save until something changes", () => {
    render(<BillingContactCard profile={profile({ city: "Atlanta" })} />)

    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
  })
})
