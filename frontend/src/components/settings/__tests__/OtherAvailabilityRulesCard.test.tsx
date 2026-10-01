// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every saved rule has a home on Your hours. A rule for one appointment type,
 * or a weekly cap, is listed here with what it governs and a way to remove
 * it; the general cards (grid, limits, blocked time) never show it as theirs.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { OtherAvailabilityRulesCard, otherRules, scopeLabel } from "../OtherAvailabilityRulesCard"
import { schedulingDefaultsFromRules } from "../AvailabilitySettings"
import type { AvailabilityRule } from "@/types/availability"

const mutateDelete = vi.fn()
let rulesData: AvailabilityRule[] = []

vi.mock("@/hooks/useAvailability", () => ({
  useAvailabilityRules: () => ({ data: { data: rulesData, total: rulesData.length } }),
  useDeleteAvailabilityRule: () => ({ mutate: mutateDelete, isPending: false }),
}))
vi.mock("@/hooks/useAppointmentTypes", () => ({
  useAppointmentTypes: () => ({ data: { data: [{ id: "type-intake", name: "Intake" }] } }),
}))

function rule(overrides: Partial<AvailabilityRule>): AvailabilityRule {
  return {
    id: "r",
    user_id: "u",
    rule_type: "max_per_day",
    enforcement: "hard",
    params: { max: 6 },
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

const generalDailyCap = rule({ id: "daily", rule_type: "max_per_day", params: { max: 6 } })
const generalWeeklyCap = rule({ id: "weekly", rule_type: "max_per_week", params: { max: 20 } })
const intakeWeeklyCap = rule({
  id: "intake-weekly",
  rule_type: "max_per_week",
  params: { max: 2 },
  appointment_type_id: "type-intake",
})
const intakeTuesdays = rule({
  id: "intake-tue",
  rule_type: "working_hours",
  params: { day_of_week: 1, start: "13:00", end: "17:00" },
  appointment_type_id: "type-intake",
  allow_other_types: false,
})
const intakeBuffer = rule({
  id: "intake-buffer",
  rule_type: "buffer_after",
  params: { minutes: 30 },
  appointment_type_id: "type-intake",
})

describe("otherRules", () => {
  it("keeps type-scoped rules and general rules no other card shows", () => {
    expect(
      otherRules([generalDailyCap, generalWeeklyCap, intakeWeeklyCap, intakeTuesdays]).map((r) => r.id)
    ).toEqual(["weekly", "intake-weekly", "intake-tue"])
  })
})

describe("scopeLabel", () => {
  it("names the type, and says when a window is claimed for it alone", () => {
    expect(scopeLabel(generalWeeklyCap, null)).toBeNull()
    expect(scopeLabel(intakeWeeklyCap, "Intake")).toBe("Intake only")
    expect(scopeLabel(intakeTuesdays, "Intake")).toBe("Only Intake in this time")
  })
})

describe("the general limits ignore a type's own rules", () => {
  it("does not read an intake buffer as the practice's break between sessions", () => {
    expect(schedulingDefaultsFromRules([intakeBuffer]).breakMinutes).toBe("0")
  })
})

describe("OtherAvailabilityRulesCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    rulesData = [generalDailyCap, intakeWeeklyCap, intakeTuesdays]
  })

  it("lists each rule with what it governs, and removes the one asked", async () => {
    const user = userEvent.setup()
    render(<OtherAvailabilityRulesCard />)

    expect(screen.getByText(/Max 2 appointments per week · Intake only/)).toBeInTheDocument()
    expect(screen.getByText(/Only Intake in this time/)).toBeInTheDocument()
    // The general daily cap belongs to the limits card, not this one.
    expect(screen.queryByText(/Max 6 appointments per day/)).toBeNull()

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0])
    expect(mutateDelete.mock.calls[0][0]).toBe("intake-weekly")
  })

  it("renders nothing when every rule already has a home", () => {
    rulesData = [generalDailyCap]
    const { container } = render(<OtherAvailabilityRulesCard />)
    expect(container).toBeEmptyDOMElement()
  })
})
