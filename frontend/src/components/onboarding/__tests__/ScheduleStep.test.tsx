// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, fireEvent, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { renderWithProviders } from "@/test/renderWithProviders"
import { ScheduleStep } from "../ScheduleStep"

const push = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}))

const createAvailabilityRule = vi.fn().mockResolvedValue({})
vi.mock("@/lib/api/availability", () => ({
  createAvailabilityRule: (...args: unknown[]) => createAvailabilityRule(...args),
}))

const updateUserProfile = vi.fn().mockResolvedValue({})
vi.mock("@/lib/api/users", () => ({
  updateUserProfile: (...args: unknown[]) => updateUserProfile(...args),
}))

const trackOnboardingStepSkipped = vi.fn()
vi.mock("@/lib/analytics/onboarding", () => ({
  trackOnboardingStepSkipped: (...args: unknown[]) => trackOnboardingStepSkipped(...args),
}))

function render() {
  return renderWithProviders(<ScheduleStep />)
}

describe("ScheduleStep", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    createAvailabilityRule.mockResolvedValue({})
    updateUserProfile.mockResolvedValue({})
  })

  it("saves a working_hours rule per selected day, then completes onboarding, then navigates", async () => {
    render()

    // Default selection is Mon-Fri; uncheck Tue and Thu to leave Mon/Wed/Fri.
    fireEvent.click(screen.getByLabelText("Tuesday"))
    fireEvent.click(screen.getByLabelText("Thursday"))

    fireEvent.click(screen.getByText("Save"))

    await waitFor(() => expect(updateUserProfile).toHaveBeenCalledTimes(1))

    expect(createAvailabilityRule).toHaveBeenCalledTimes(3)
    expect(createAvailabilityRule.mock.calls[0][0]).toEqual({
      rule_type: "working_hours",
      enforcement: "hard",
      params: { day_of_week: 0, start: "09:00", end: "17:00" },
    })
    expect(createAvailabilityRule.mock.calls[1][0]).toEqual({
      rule_type: "working_hours",
      enforcement: "hard",
      params: { day_of_week: 2, start: "09:00", end: "17:00" },
    })
    expect(createAvailabilityRule.mock.calls[2][0]).toEqual({
      rule_type: "working_hours",
      enforcement: "hard",
      params: { day_of_week: 4, start: "09:00", end: "17:00" },
    })

    expect(updateUserProfile).toHaveBeenCalledWith({ onboarding_state: "completed" })

    // Rules are created before the profile update — assert relative call order.
    const lastRuleCallOrder = createAvailabilityRule.mock.invocationCallOrder[2]
    const profileCallOrder = updateUserProfile.mock.invocationCallOrder[0]
    expect(lastRuleCallOrder).toBeLessThan(profileCallOrder)

    await waitFor(() => expect(push).toHaveBeenCalledWith("/onboarding"))
  })

  it("skipping creates no rules, completes onboarding, emits analytics, and navigates", async () => {
    render()

    fireEvent.click(screen.getByText("Skip for now"))

    await waitFor(() => expect(updateUserProfile).toHaveBeenCalledTimes(1))

    expect(createAvailabilityRule).not.toHaveBeenCalled()
    expect(updateUserProfile).toHaveBeenCalledWith({ onboarding_state: "completed" })
    expect(trackOnboardingStepSkipped).toHaveBeenCalledWith("schedule")
    await waitFor(() => expect(push).toHaveBeenCalledWith("/onboarding"))
  })

  it("disables Save when no weekday is checked", () => {
    render()

    for (const day of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]) {
      fireEvent.click(screen.getByLabelText(day))
    }

    expect(screen.getByText("Save").closest("button")).toBeDisabled()
  })

  it("disables Save when end <= start", async () => {
    const user = userEvent.setup()
    render()

    await user.click(screen.getByRole("combobox", { name: /end/i }))
    await user.click(screen.getByRole("option", { name: "09:00" }))

    expect(screen.getByText("Save").closest("button")).toBeDisabled()
  })
})
