// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A rule for one appointment type has a home on Your hours, and only one.
 *
 * What only the real stack can prove: the create route stores the type scope
 * a confirmed proposal carries, the settings page lists that rule with the
 * type it governs, the general working-hours grid does not mistake an intake
 * window for the practice's hours, and Remove deletes exactly that rule.
 *
 * Deliberately not here: the sentence-to-proposal step. It needs a model,
 * which the local stack does not run; it is graded against the real model in
 * backend/evals/availability_parse and its validation is pinned in
 * backend/tests/test_availability_parse_service.py.
 */

import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"

interface AppointmentType {
  id: string
  name: string
}

interface Rule {
  id: string
  rule_type: string
  appointment_type_id?: string | null
  allow_other_types?: boolean
}

async function intakeTypeId(api: ApiClient): Promise<string> {
  // Listing seeds the practice's default types (Session, Consultation, Intake).
  const { data } = await api.get<{ data: AppointmentType[] }>("/api/appointment-types")
  const intake = data.find((t) => t.name === "Intake")
  if (!intake) throw new Error("the default Intake type was not seeded")
  return intake.id
}

test.describe("Rules for one appointment type", () => {
  const created: string[] = []

  test.afterEach(async ({ api }) => {
    for (const id of created.splice(0)) {
      await api.delete(`/api/availability/rules/${id}`).catch(() => undefined)
    }
  })

  test("are stored with their type, listed under More rules, and kept out of the general hours", async ({
    api,
    signedInPage: page,
  }) => {
    const intake = await intakeTypeId(api)

    const cap = await api.post<Rule>("/api/availability/rules", {
      rule_type: "max_per_week",
      enforcement: "hard",
      params: { max: 2 },
      appointment_type_id: intake,
      allow_other_types: true,
    })
    created.push(cap.id)
    expect(cap.appointment_type_id).toBe(intake)

    const window = await api.post<Rule>("/api/availability/rules", {
      rule_type: "working_hours",
      enforcement: "hard",
      params: { day_of_week: 2, start: "13:00", end: "17:00" },
      appointment_type_id: intake,
      allow_other_types: false,
    })
    created.push(window.id)

    await page.goto("/dashboard/settings/availability")

    const more = page.getByTestId("other-availability-rules")
    await expect(more).toBeVisible()
    await expect(more.getByText(/Max 2 appointments per week · Intake only/)).toBeVisible()
    await expect(more.getByText(/Only Intake in this time/)).toBeVisible()

    // Wednesday's intake window is not the practice's Wednesday hours.
    await expect(page.getByRole("switch", { name: "Wednesday on" })).toHaveAttribute(
      "aria-checked",
      "false",
    )

    // Remove deletes that rule and nothing else.
    const capRow = more.locator("li", { hasText: "Intake only" }).filter({ hasText: "per week" })
    await capRow.getByRole("button", { name: "Remove" }).click()
    await expect(more.getByText(/Max 2 appointments per week/)).toHaveCount(0)

    const { data: rules } = await api.get<{ data: Rule[] }>("/api/availability/rules")
    expect(rules.map((r) => r.id)).toContain(window.id)
    expect(rules.map((r) => r.id)).not.toContain(cap.id)
  })
})
