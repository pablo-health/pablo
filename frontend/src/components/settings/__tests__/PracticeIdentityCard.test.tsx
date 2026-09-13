// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Practice identity — legal name, tax ID, billing NPI.
 *
 * The tax ID is the field under test: never pre-filled, masked to its last
 * four once on file, changed only through an explicit action, and sent only
 * when typed. The card saves ONLY its own fields, so the billing contact it
 * sits beside cannot be blanked by saving this half.
 *
 * The billing NPI carries a second bug class: it was once hidden from a sole
 * proprietor filing under an SSN, who was then told by the clearinghouse
 * banner that she was missing it. A field the product refuses to show and
 * then demands is a dead end with no exit, so the tests below pin that it
 * stays reachable for her.
 */

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { BillingProfileResponse } from "@/types/practiceBilling"
import { PracticeIdentityCard } from "../PracticeIdentityCard"

const mockUpdate = vi.fn()

vi.mock("@/hooks/useBillingProfile", () => ({
  useUpdateBillingProfile: () => ({ mutate: mockUpdate, isPending: false }),
}))

vi.mock("../SettingsSavedContext", () => ({
  useSettingsSaved: () => ({ flashSaved: vi.fn() }),
}))

let clinicianNpi: string | null = null

vi.mock("../useSettingsPreferences", () => ({
  useSettingsUserStatus: () => ({ data: { npi_number: clinicianNpi } }),
}))

function profile(overrides: Partial<BillingProfileResponse> = {}): BillingProfileResponse {
  return {
    legal_name: null,
    tax_id_last4: null,
    tax_id_type: null,
    billing_npi: null,
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

const onFile = () =>
  profile({
    legal_name: "Acme Therapy LLC",
    tax_id_last4: "9714",
    tax_id_type: "ein",
    address_line1: "1 Test St",
    city: "Atlanta",
    phone: "4045550100",
  })

beforeEach(() => {
  vi.clearAllMocks()
  clinicianNpi = null
})

describe("the tax ID", () => {
  it("offers an empty box when none is on file", () => {
    render(<PracticeIdentityCard profile={profile()} />)

    expect(screen.getByTestId("tax-id-input")).toHaveValue("")
  })

  it("shows only the last four once one is stored, never the number", () => {
    render(<PracticeIdentityCard profile={onFile()} />)

    expect(screen.getByText("Ends in 9714")).toBeInTheDocument()
    expect(screen.queryByTestId("tax-id-input")).not.toBeInTheDocument()
  })

  it("changes through an explicit action, starting from an empty field", async () => {
    const user = userEvent.setup()
    render(<PracticeIdentityCard profile={onFile()} />)

    await user.click(screen.getByRole("button", { name: "Change" }))

    expect(screen.getByTestId("tax-id-input")).toHaveValue("")
  })

  it("is not sent when it was never typed", async () => {
    const user = userEvent.setup()
    render(<PracticeIdentityCard profile={onFile()} />)

    await user.type(screen.getByLabelText("Legal business name"), "!")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockUpdate).toHaveBeenCalledWith(
      expect.not.objectContaining({ tax_id: expect.anything() }),
      expect.anything(),
    )
  })

  it("refuses one that is not nine digits", async () => {
    const user = userEvent.setup()
    render(<PracticeIdentityCard profile={profile({ tax_id_type: "ein" })} />)

    await user.type(screen.getByTestId("tax-id-input"), "12345")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(screen.getByRole("alert")).toHaveTextContent("nine digits")
    expect(mockUpdate).not.toHaveBeenCalled()
  })
})

describe("the billing NPI", () => {
  it("stays reachable for a sole proprietor filing under an SSN", async () => {
    // It used to be hidden from her, on the reasoning that an ORGANISATION
    // NPI means nothing to a sole proprietor. True, and the wrong field: the
    // clearinghouse wants the NPI a claim is billed under, which for her is
    // her own. Hidden, she could not fill the one thing registration then
    // refused her for.
    const user = userEvent.setup()
    render(<PracticeIdentityCard profile={profile()} />)

    await user.click(screen.getByRole("radio", { name: "SSN" }))

    expect(screen.getByLabelText("Billing NPI")).toBeInTheDocument()
  })

  it("still shows when she already has one, whatever the tax ID type", async () => {
    // A value on file must never silently disappear.
    const user = userEvent.setup()
    render(<PracticeIdentityCard profile={profile({ billing_npi: "1999999984" })} />)

    await user.click(screen.getByRole("radio", { name: "SSN" }))

    expect(screen.getByLabelText("Billing NPI")).toHaveValue("1999999984")
  })

  it("refuses one that is not ten digits", async () => {
    const user = userEvent.setup()
    render(<PracticeIdentityCard profile={profile()} />)

    await user.type(screen.getByLabelText("Billing NPI"), "12345")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(screen.getByRole("alert")).toHaveTextContent("ten digits")
    expect(mockUpdate).not.toHaveBeenCalled()
  })

  it("offers her own NPI when she files under an SSN", async () => {
    clinicianNpi = "1999999984"
    const user = userEvent.setup()
    render(<PracticeIdentityCard profile={profile()} />)

    await user.click(screen.getByRole("radio", { name: "SSN" }))
    await user.click(screen.getByRole("button", { name: "Use my own NPI" }))

    expect(screen.getByLabelText("Billing NPI")).toHaveValue("1999999984")
  })

  it("does not offer it under an EIN, where the practice is the biller", async () => {
    // Her personal NPI would be the wrong answer for an entity, and a wrong
    // prefill is worse than none: the (NPI, tax ID) pair is what the
    // clearinghouse hangs the provider record on.
    clinicianNpi = "1999999984"
    const user = userEvent.setup()
    render(<PracticeIdentityCard profile={profile()} />)

    await user.click(screen.getByRole("radio", { name: "EIN" }))

    expect(screen.queryByRole("button", { name: "Use my own NPI" })).not.toBeInTheDocument()
  })

  it("does not offer it when she has no NPI of her own", async () => {
    const user = userEvent.setup()
    render(<PracticeIdentityCard profile={profile()} />)

    await user.click(screen.getByRole("radio", { name: "SSN" }))

    expect(screen.queryByRole("button", { name: "Use my own NPI" })).not.toBeInTheDocument()
  })
})

describe("saving its own half only", () => {
  it("sends the identity fields and nothing the contact card owns", async () => {
    // The two cards save independently. If this one sent contact fields it
    // would blank them, because the endpoint merges what it is given.
    const user = userEvent.setup()
    render(<PracticeIdentityCard profile={onFile()} />)

    await user.clear(screen.getByLabelText("Legal business name"))
    await user.type(screen.getByLabelText("Legal business name"), "New Name LLC")
    await user.click(screen.getByRole("button", { name: "Save" }))

    const [patch] = mockUpdate.mock.calls[0]
    expect(patch).toEqual({ legal_name: "New Name LLC" })
  })

  it("never blanks the legal name, because an empty box is usually a mis-click", async () => {
    const user = userEvent.setup()
    render(<PracticeIdentityCard profile={onFile()} />)

    await user.clear(screen.getByLabelText("Legal business name"))

    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
  })
})
