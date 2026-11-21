// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * EligibilityChecksCard tests — the auto-check switch in Settings.
 *
 * On by default for a practice that has never saved a billing profile;
 * flipping it sends only that field.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { EligibilityChecksCard } from "../EligibilityChecksCard"

const mockUseBillingProfile = vi.fn()
const mockUpdate = vi.fn()

vi.mock("@/hooks/useBillingProfile", () => ({
  useBillingProfile: (...args: unknown[]) => mockUseBillingProfile(...args),
  useUpdateBillingProfile: () => ({ mutate: mockUpdate, isPending: false }),
}))

let isPracticeAdmin = true

vi.mock("../useSettingsPreferences", () => ({
  useSettingsUserStatus: () => ({ data: { is_practice_admin: isPracticeAdmin } }),
}))

describe("EligibilityChecksCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    isPracticeAdmin = true
  })

  it("reads as on when the practice has it on", () => {
    mockUseBillingProfile.mockReturnValue({ data: { eligibility_auto_check: true } })

    render(<EligibilityChecksCard />)

    expect(screen.getByRole("switch")).toBeChecked()
  })

  it("turns it off with a single-field update", async () => {
    mockUseBillingProfile.mockReturnValue({ data: { eligibility_auto_check: true } })
    const user = userEvent.setup()

    render(<EligibilityChecksCard />)
    await user.click(screen.getByRole("switch"))

    expect(mockUpdate).toHaveBeenCalledWith({ eligibility_auto_check: false })
  })

  it("waits for the profile before allowing a change", () => {
    mockUseBillingProfile.mockReturnValue({ data: undefined })

    render(<EligibilityChecksCard />)

    expect(screen.getByRole("switch")).toBeDisabled()
    expect(screen.getByRole("switch")).toBeChecked()
  })

  it("is read-only, with the reason, for a clinician who is not an admin", async () => {
    isPracticeAdmin = false
    mockUseBillingProfile.mockReturnValue({ data: { eligibility_auto_check: true } })
    const user = userEvent.setup()

    render(<EligibilityChecksCard />)

    expect(screen.getByTestId("practice-admin-only")).toBeInTheDocument()
    expect(screen.getByRole("switch")).toBeDisabled()
    await user.click(screen.getByRole("switch"))
    expect(mockUpdate).not.toHaveBeenCalled()
  })
})
