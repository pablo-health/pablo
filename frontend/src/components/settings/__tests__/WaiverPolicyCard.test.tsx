// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * WaiverPolicyCard tests — the two practice-level write-off switches.
 *
 * The courtesy toggle saves on change, like every other settings switch;
 * the small-balance threshold is a dollar field with its own Save button
 * because a typed amount should land as one deliberate act.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { WaiverPolicyCard } from "../WaiverPolicyCard"

const mockUseBillingProfile = vi.fn()
const mockUpdate = vi.fn()

vi.mock("@/hooks/useBillingProfile", () => ({
  useBillingProfile: (...args: unknown[]) => mockUseBillingProfile(...args),
  useUpdateBillingProfile: () => ({ mutate: mockUpdate, isPending: false }),
}))

vi.mock("../SettingsSavedContext", () => ({
  useSettingsSaved: () => ({ saved: false, flashSaved: vi.fn() }),
}))

let isPracticeAdmin = true

vi.mock("../useSettingsPreferences", () => ({
  useSettingsUserStatus: () => ({ data: { is_practice_admin: isPracticeAdmin } }),
}))

describe("WaiverPolicyCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    isPracticeAdmin = true
  })

  it("reads the courtesy switch off by default", () => {
    mockUseBillingProfile.mockReturnValue({
      data: { allow_courtesy_writeoffs: false, small_balance_cents: 500 },
    })

    render(<WaiverPolicyCard />)

    expect(screen.getByRole("switch")).not.toBeChecked()
    expect(screen.getByDisplayValue("5.00")).toBeInTheDocument()
  })

  it("turns courtesy write-offs on with a single-field update", async () => {
    mockUseBillingProfile.mockReturnValue({
      data: { allow_courtesy_writeoffs: false, small_balance_cents: 500 },
    })
    const user = userEvent.setup()

    render(<WaiverPolicyCard />)
    await user.click(screen.getByRole("switch"))

    expect(mockUpdate).toHaveBeenCalledWith({ allow_courtesy_writeoffs: true })
  })

  it("saves an edited threshold in cents", async () => {
    mockUseBillingProfile.mockReturnValue({
      data: { allow_courtesy_writeoffs: false, small_balance_cents: 500 },
    })
    const user = userEvent.setup()

    render(<WaiverPolicyCard />)
    const input = screen.getByDisplayValue("5.00")
    await user.clear(input)
    await user.type(input, "12.50")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockUpdate).toHaveBeenCalledWith(
      { small_balance_cents: 1250 },
      expect.anything(),
    )
  })

  it("is read-only, with the reason, for a clinician who is not an admin", async () => {
    isPracticeAdmin = false
    mockUseBillingProfile.mockReturnValue({
      data: { allow_courtesy_writeoffs: false, small_balance_cents: 500 },
    })
    const user = userEvent.setup()

    render(<WaiverPolicyCard />)

    expect(screen.getByTestId("practice-admin-only")).toBeInTheDocument()
    expect(screen.getByRole("switch")).toBeDisabled()
    const input = screen.getByDisplayValue("5.00")
    expect(input).toBeDisabled()
    await user.click(screen.getByRole("switch"))
    expect(mockUpdate).not.toHaveBeenCalled()
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
  })

  it("shows no read-only line to an admin", () => {
    mockUseBillingProfile.mockReturnValue({
      data: { allow_courtesy_writeoffs: false, small_balance_cents: 500 },
    })

    render(<WaiverPolicyCard />)

    expect(screen.queryByTestId("practice-admin-only")).not.toBeInTheDocument()
    expect(screen.getByRole("switch")).toBeEnabled()
  })
})
