// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice with no hours opens the calendar for the first time, describes
 * its hours in a sentence, confirms the reading, and then sees them: free
 * time on the days it named and none on the others, in the calendar's week
 * view and in the Settings hours grid alike.
 *
 * Everything goes through the screen. The sentence is read by the stack's
 * stand-in for the model (scripts/fake_llm.py), which gives the same reading
 * every run; the rules it proposes are saved by the same requests a
 * clinician's confirmation makes, and the week view's shading comes from the
 * engine's own free-slot answer for each day.
 *
 * Its own practice (see fixtures/freshPractice.ts), because the step is only
 * asked of a practice with no rules at all.
 */

import type { Locator, Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { BROWSER_TIME_ZONE } from "../fixtures/clock"
import { signInToFreshPractice } from "../fixtures/freshPractice"

test.describe.configure({ retries: 0 })

const SENTENCE = "9 to 5 Monday to Thursday"
const WORKING_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday"]
const DAYS_OFF = ["Friday", "Saturday", "Sunday"]

/** Back to a practice that has never been asked: no rules, setup not done. */
async function startFresh(api: ApiClient): Promise<void> {
  const rules = await api.get<{ data: { id: string }[] }>("/api/availability/rules")
  for (const rule of rules.data) await api.delete(`/api/availability/rules/${rule.id}`)
  const preferences = await api.get<Record<string, unknown>>("/api/users/me/preferences")
  await api.put("/api/users/me/preferences", {
    ...preferences,
    calendar_setup_complete: false,
    calendar_default_view: "timeGridWeek",
    timezone: BROWSER_TIME_ZONE,
  })
}

function weekColumn(page: Page, day: string): Locator {
  return page.locator(
    `[aria-label^="${day} "][aria-label$=" schedule. Click to add appointment."]`,
  )
}

/** How much of a day's column is shaded unavailable, as a fraction. */
async function shadedFraction(column: Locator): Promise<number> {
  return column.evaluate((element) => {
    const height = element.getBoundingClientRect().height
    let shaded = 0
    for (const band of element.querySelectorAll(".ed-unavailable")) {
      shaded += band.getBoundingClientRect().height
    }
    return height > 0 ? shaded / height : 0
  })
}

test("hours described on first run show up as free time in the week and in Settings", async ({
  browser,
}) => {
  const { page, context, api } = await signInToFreshPractice(browser, "hours", {
    timezoneId: BROWSER_TIME_ZONE,
  })
  try {
    await startFresh(api)

    await page.goto("/dashboard/calendar")
    await expect(page.getByRole("heading", { name: "When do you see clients?" })).toBeVisible()

    await page.getByLabel("Tell Pablo in your own words").fill(SENTENCE)
    await page.getByRole("button", { name: "Check this" }).click()
    await expect(page.getByText("Monday to Thursday, 9:00 AM to 5:00 PM")).toBeVisible()
    await page.getByRole("button", { name: "Yes, save this" }).click()

    // Every day is saved, and only then does the wizard move on — to
    // Google, the step it had put the hours in front of, not past it. This
    // practice leaves Google for later.
    await expect
      .poll(async () => {
        const saved = await api.get<{ data: { params: { day_of_week: number } }[] }>(
          "/api/availability/rules",
        )
        return saved.data.map((rule) => rule.params.day_of_week).sort()
      })
      .toEqual([0, 1, 2, 3])
    await expect(page.getByRole("heading", { name: "Connect Google Calendar" })).toBeVisible()
    await page.getByRole("button", { name: "Finish later" }).click()

    await expect(page.getByLabel("Weekly appointment calendar")).toBeVisible()

    // A working day is shaded outside its hours and free inside them; a day
    // with no hours is shaded from top to bottom. Polled, because each
    // column's shading arrives with that day's own free-slot answer.
    for (const day of WORKING_DAYS) {
      const column = weekColumn(page, day)
      await expect
        .poll(() => shadedFraction(column), { message: `${day} has free time` })
        .toBeGreaterThan(0)
      expect(await shadedFraction(column), `${day} is not shaded throughout`).toBeLessThan(0.9)
    }
    for (const day of DAYS_OFF) {
      await expect
        .poll(() => shadedFraction(weekColumn(page, day)), { message: `${day} has no free time` })
        .toBeGreaterThan(0.99)
    }

    await page.goto("/dashboard/settings/availability")
    for (const day of WORKING_DAYS) {
      await expect(page.getByRole("switch", { name: `${day} on` })).toHaveAttribute(
        "aria-checked",
        "true",
      )
      await expect(page.getByRole("combobox", { name: `${day} start` })).toHaveText("9:00 AM")
      await expect(page.getByRole("combobox", { name: `${day} end` })).toHaveText("5:00 PM")
    }
    for (const day of DAYS_OFF) {
      await expect(page.getByRole("switch", { name: `${day} on` })).toHaveAttribute(
        "aria-checked",
        "false",
      )
    }
  } finally {
    await context.close()
  }
})
