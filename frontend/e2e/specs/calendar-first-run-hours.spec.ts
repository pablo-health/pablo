// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice with no hours opens the calendar for the first time, describes
 * its hours in a sentence, confirms the reading, and then sees them: working
 * time on the days it named and none on the others, in the calendar's week
 * view and in the Settings hours grid alike.
 *
 * The browser's clock is set to a Friday evening, the case that once looked
 * as if nothing had been saved: with Fridays off, nothing in the current
 * week is still ahead. The calendar opens on the week the hours start, and
 * the week just gone still shows its working days — marked past, not blank.
 *
 * Everything goes through the screen. The sentence is read by the stack's
 * stand-in for the model (scripts/fake_llm.py), which gives the same reading
 * every run, and the rules it proposes are saved by the same requests a
 * clinician's confirmation makes.
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

const DAY_MS = 24 * 60 * 60 * 1000

/** The calendar date a moment falls on in the browser's zone. */
function browserDate(at: Date): { weekday: string; label: string } {
  const part = (options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat("en-US", { timeZone: BROWSER_TIME_ZONE, ...options }).format(at)
  return {
    weekday: part({ weekday: "long" }),
    label: `${part({ weekday: "long" })} ${part({ month: "short" })} ${part({ day: "numeric" })}`,
  }
}

/**
 * 7 PM on the most recent Friday in the browser's zone that is already
 * behind us. In the past rather than ahead, so the sign-in token the stack
 * issued a moment ago still reads as current under the browser's clock.
 */
function lastFridayEvening(): Date {
  const now = Date.now()
  for (let back = 0; back < 8; back++) {
    // 23:00 UTC is 7 PM in New York in daylight time and 6 PM in standard
    // time; the hour is corrected below from the zone's own answer.
    const day = new Date(now - back * DAY_MS)
    if (browserDate(day).weekday !== "Friday") continue
    const ymd = new Intl.DateTimeFormat("en-CA", { timeZone: BROWSER_TIME_ZONE }).format(day)
    const [y, m, d] = ymd.split("-").map(Number)
    const guess = new Date(Date.UTC(y, m - 1, d, 23, 0))
    const hour = Number(
      new Intl.DateTimeFormat("en-US", {
        timeZone: BROWSER_TIME_ZONE,
        hour: "numeric",
        hourCycle: "h23",
      }).format(guess),
    )
    const evening = new Date(guess.getTime() + (19 - hour) * 60 * 60 * 1000)
    if (evening.getTime() < now) return evening
  }
  throw new Error("no Friday evening in the last week")
}

function weekColumn(page: Page, label: string): Locator {
  return page.locator(
    `[aria-label^="${label} "][aria-label$=" schedule. Click to add appointment."]`,
  )
}

/** How much of a day's column is covered by bands of *selector*, as a fraction. */
async function coveredFraction(column: Locator, selector: string): Promise<number> {
  return column.evaluate((element, bandSelector) => {
    const height = element.getBoundingClientRect().height
    let covered = 0
    for (const band of element.querySelectorAll(bandSelector)) {
      covered += band.getBoundingClientRect().height
    }
    return height > 0 ? covered / height : 0
  }, selector)
}

/** The seven columns of the week holding *sunday*, by their labels. */
function weekFrom(sunday: Date): { label: string; weekday: string }[] {
  return Array.from({ length: 7 }, (_, i) => browserDate(new Date(sunday.getTime() + i * DAY_MS)))
}

test("hours described on a Friday evening show up as working time in the week and in Settings", async ({
  browser,
}) => {
  const { page, context, api } = await signInToFreshPractice(browser, "hours", {
    timezoneId: BROWSER_TIME_ZONE,
  })
  try {
    await startFresh(api)

    const fridayEvening = lastFridayEvening()
    await page.clock.install({ time: fridayEvening })
    const thisSunday = new Date(fridayEvening.getTime() - 5 * DAY_MS)
    const nextSunday = new Date(fridayEvening.getTime() + 2 * DAY_MS)

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

    // Nothing is left in this week, so the calendar opens on the next one. A
    // working day is shaded outside its hours and open inside them; a day
    // with no hours is shaded from top to bottom; none of it is past yet.
    for (const day of weekFrom(nextSunday)) {
      const column = weekColumn(page, day.label)
      await expect(column, `${day.label} is in the week shown`).toBeVisible()
      if (WORKING_DAYS.includes(day.weekday)) {
        await expect
          .poll(() => coveredFraction(column, ".ed-unavailable"), {
            message: `${day.label} has time outside its hours`,
          })
          .toBeGreaterThan(0)
        expect(
          await coveredFraction(column, ".ed-unavailable"),
          `${day.label} has working time`,
        ).toBeLessThan(0.9)
      } else {
        await expect
          .poll(() => coveredFraction(column, ".ed-unavailable"), {
            message: `${day.label} has no working time`,
          })
          .toBeGreaterThan(0.99)
      }
      expect(await coveredFraction(column, ".ed-past"), `${day.label} is ahead`).toBe(0)
    }

    // The week the hours were set in still shows them: its working days are
    // working time, marked past rather than blank.
    await page.getByRole("button", { name: "Previous" }).click()
    for (const day of weekFrom(thisSunday)) {
      if (!WORKING_DAYS.includes(day.weekday)) continue
      const column = weekColumn(page, day.label)
      await expect
        .poll(() => coveredFraction(column, ".ed-past"), { message: `${day.label} is past` })
        .toBeGreaterThan(0.99)
      const offHours = await coveredFraction(column, ".ed-unavailable")
      expect(offHours, `${day.label} keeps its hours`).toBeGreaterThan(0)
      expect(offHours, `${day.label} is not blank`).toBeLessThan(0.9)
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
