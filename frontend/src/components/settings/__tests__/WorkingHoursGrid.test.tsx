// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { WorkingHoursGrid } from "../WorkingHoursGrid"
import type { AvailabilityRule } from "@/types/availability"

const mutateCreate = vi.fn()
const mutateUpdate = vi.fn()
const mutateDelete = vi.fn()

let rulesData: AvailabilityRule[] = []

vi.mock("@/hooks/useAvailability", () => ({
  useAvailabilityRules: () => ({
    data: { data: rulesData, total: rulesData.length },
    isLoading: false,
  }),
  useCreateAvailabilityRule: () => ({ mutate: mutateCreate, isPending: false }),
  useUpdateAvailabilityRule: () => ({ mutate: mutateUpdate, isPending: false }),
  useDeleteAvailabilityRule: () => ({ mutate: mutateDelete, isPending: false }),
}))

vi.mock("@/hooks/usePreferences", () => ({
  usePreferences: () => ({ data: { timezone: "America/New_York" } }),
}))

function makeRule(overrides: Partial<AvailabilityRule> = {}): AvailabilityRule {
  return {
    id: "rule_1",
    user_id: "user_1",
    rule_type: "working_hours",
    enforcement: "hard",
    params: { day_of_week: 0, start: "09:00", end: "17:00" },
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

describe("WorkingHoursGrid", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    rulesData = []
  })

  it("shows all seven days, off by default with no rules", () => {
    render(<WorkingHoursGrid />)

    for (const day of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]) {
      expect(screen.getByRole("switch", { name: `${day} on` })).toHaveAttribute("aria-checked", "false")
    }
  })

  it("creates a working_hours rule when a day is toggled on", async () => {
    const user = userEvent.setup()
    render(<WorkingHoursGrid />)

    await user.click(screen.getByRole("switch", { name: "Monday on" }))

    expect(mutateCreate).toHaveBeenCalledWith({
      rule_type: "working_hours",
      enforcement: "hard",
      params: { day_of_week: 0, start: "09:00", end: "17:00" },
    })
  })

  it("deletes the day's rule when toggled off", async () => {
    rulesData = [makeRule({ id: "mon", params: { day_of_week: 0, start: "09:00", end: "17:00" } })]
    const user = userEvent.setup()
    render(<WorkingHoursGrid />)

    await user.click(screen.getByRole("switch", { name: "Monday on" }))

    expect(mutateDelete).toHaveBeenCalledWith("mon")
  })

  it("seeds Monday to Friday at 9 to 5 from the empty state", async () => {
    const user = userEvent.setup()
    render(<WorkingHoursGrid />)

    await user.click(screen.getByRole("button", { name: "Set Monday to Friday, 9 to 5" }))

    expect(mutateCreate).toHaveBeenCalledTimes(5)
    for (let dayOfWeek = 0; dayOfWeek <= 4; dayOfWeek++) {
      expect(mutateCreate).toHaveBeenCalledWith({
        rule_type: "working_hours",
        enforcement: "hard",
        params: { day_of_week: dayOfWeek, start: "09:00", end: "17:00" },
      })
    }
  })

  it("shows the derived window and does not offer the seed button once a rule exists", () => {
    rulesData = [
      makeRule({ id: "mon", params: { day_of_week: 0, start: "09:00", end: "17:00" } }),
      makeRule({ id: "sat", params: { day_of_week: 5, start: "10:00", end: "14:00" } }),
    ]

    render(<WorkingHoursGrid />)

    expect(screen.getByRole("switch", { name: "Saturday on" })).toHaveAttribute("aria-checked", "true")
    const footer = screen.getByTestId("working-hours-footer")
    expect(footer).toHaveTextContent("9 AM")
    expect(footer).toHaveTextContent("5 PM")
    expect(screen.queryByRole("button", { name: "Set Monday to Friday, 9 to 5" })).not.toBeInTheDocument()
  })
})
