// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * BillingProfileCard tests — the practice's billing identity form.
 *
 * The tax id is the field under test: never pre-filled, masked to its last
 * four once on file, replaced only through an explicit action, and sent
 * only when typed. Everything else saves as a partial patch of what changed.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { BillingProfileResponse } from "@/types/practiceBilling"

import { BillingProfileCard } from "../BillingProfileCard"

const mockUpdate = vi.fn()

vi.mock("@/hooks/useBillingProfile", () => ({
  useUpdateBillingProfile: () => ({ mutate: mockUpdate, isPending: false }),
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
    state: "GA",
    postal_code: "30301",
    phone: "4045550100",
    contact_email: "billing@example.com",
  })

describe("BillingProfileCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("offers an empty tax id field to a practice with none on file", () => {
    render(<BillingProfileCard profile={profile()} />)

    expect(screen.getByTestId("tax-id-input")).toHaveValue("")
    expect(screen.queryByTestId("tax-id-masked")).not.toBeInTheDocument()
  })

  it("shows only the last four once a tax id is on file, and never the number", () => {
    render(<BillingProfileCard profile={onFile()} />)

    expect(screen.getByTestId("tax-id-masked")).toHaveTextContent("Ends in 9714")
    expect(screen.queryByTestId("tax-id-input")).not.toBeInTheDocument()
    for (const input of screen.getAllByRole("textbox")) {
      expect((input as HTMLInputElement).value).not.toMatch(/9714/)
    }
  })

  it("replaces the tax id through an explicit action, starting from an empty field", async () => {
    const user = userEvent.setup()
    render(<BillingProfileCard profile={onFile()} />)

    await user.click(screen.getByRole("button", { name: "Replace" }))

    const input = screen.getByTestId("tax-id-input")
    expect(input).toHaveValue("")
    await user.type(input, "12-3456789")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockUpdate).toHaveBeenCalledWith(
      { tax_id: "12-3456789" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })

  it("keeps the current tax id when the replacement is abandoned", async () => {
    const user = userEvent.setup()
    render(<BillingProfileCard profile={onFile()} />)

    await user.click(screen.getByRole("button", { name: "Replace" }))
    await user.type(screen.getByTestId("tax-id-input"), "1234")
    await user.click(screen.getByRole("button", { name: "Keep current" }))

    expect(screen.getByTestId("tax-id-masked")).toHaveTextContent("Ends in 9714")
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
  })

  it("sends only the fields that changed", async () => {
    const user = userEvent.setup()
    render(<BillingProfileCard profile={onFile()} />)

    await user.clear(screen.getByLabelText("Phone"))
    await user.type(screen.getByLabelText("Phone"), "4045550199")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockUpdate).toHaveBeenCalledWith({ phone: "4045550199" }, expect.anything())
  })

  it("asks for the tax id type before sending a new tax id", async () => {
    const user = userEvent.setup()
    render(<BillingProfileCard profile={profile()} />)

    await user.type(screen.getByTestId("tax-id-input"), "123456789")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(screen.getByRole("alert")).toHaveTextContent("EIN or an SSN")
    expect(mockUpdate).not.toHaveBeenCalled()

    await user.click(screen.getByRole("radio", { name: "EIN" }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockUpdate).toHaveBeenCalledWith(
      { tax_id_type: "ein", tax_id: "123456789" },
      expect.anything(),
    )
  })

  it("refuses a billing NPI that is not ten digits", async () => {
    const user = userEvent.setup()
    render(<BillingProfileCard profile={profile()} />)

    await user.type(screen.getByLabelText("Billing NPI (optional)"), "12345")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(screen.getByRole("alert")).toHaveTextContent("ten digits")
    expect(mockUpdate).not.toHaveBeenCalled()
  })

  it("offers the profile's practice details to a card with nothing on it yet", async () => {
    const user = userEvent.setup()
    render(
      <BillingProfileCard
        profile={profile()}
        practiceDetails={{
          name: "Acme Therapy LLC",
          phone: "4045550100",
          address: "1 Test St, Atlanta, GA 30301",
        }}
      />,
    )

    await user.click(screen.getByRole("button", { name: "Use my profile details" }))

    expect(screen.getByLabelText("Legal name")).toHaveValue("Acme Therapy LLC")
    expect(screen.getByLabelText("Phone")).toHaveValue("4045550100")
    // The profile keeps one free-text line, so it lands whole on line 1 and
    // the city/state/ZIP stay empty for the therapist to split out.
    expect(screen.getByLabelText("Billing address")).toHaveValue("1 Test St, Atlanta, GA 30301")
    expect(screen.getByLabelText("City")).toHaveValue("")
    expect(screen.getByLabelText("State")).toHaveValue("")
    expect(screen.getByLabelText("ZIP")).toHaveValue("")
    expect(screen.getByTestId("tax-id-input")).toHaveValue("")
    expect(screen.getByLabelText("Billing NPI (optional)")).toHaveValue("")
    expect(mockUpdate).not.toHaveBeenCalled()
    expect(screen.queryByRole("button", { name: "Use my profile details" })).not.toBeInTheDocument()
  })

  it("offers the prefill for an address alone, when that is all the profile holds", async () => {
    const user = userEvent.setup()
    render(
      <BillingProfileCard
        profile={profile()}
        practiceDetails={{ name: null, phone: null, address: "1 Test St, Atlanta, GA 30301" }}
      />,
    )

    await user.click(screen.getByRole("button", { name: "Use my profile details" }))

    expect(screen.getByLabelText("Billing address")).toHaveValue("1 Test St, Atlanta, GA 30301")
  })

  it("does not offer the prefill once the practice profile has details of its own", () => {
    render(
      <BillingProfileCard
        profile={onFile()}
        practiceDetails={{ name: "Acme Therapy LLC", phone: "4045550100", address: "1 Test St" }}
      />,
    )

    expect(screen.queryByRole("button", { name: "Use my profile details" })).not.toBeInTheDocument()
    expect(screen.getByLabelText("Legal name")).toHaveValue("Acme Therapy LLC")
  })

  it("offers no prefill when the profile holds nothing to copy", () => {
    render(
      <BillingProfileCard
        profile={profile()}
        practiceDetails={{ name: null, phone: null, address: null }}
      />,
    )

    expect(screen.queryByRole("button", { name: "Use my profile details" })).not.toBeInTheDocument()
  })

  it("clears the tax id field after a save lands", async () => {
    const user = userEvent.setup()
    mockUpdate.mockImplementation((_patch, options) => options.onSuccess())
    render(<BillingProfileCard profile={profile()} />)

    await user.click(screen.getByRole("radio", { name: "SSN" }))
    await user.type(screen.getByTestId("tax-id-input"), "123456789")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(screen.getByTestId("tax-id-input")).toHaveValue("")
  })
})
