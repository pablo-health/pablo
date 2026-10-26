// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The calendar draws a held time as held, with when the hold lapses, and
 * selecting days on it opens the bar that carries actions on a selected date
 * range.
 *
 * The hold is made the way a real one is: a stranger books through a booking
 * link that asks them to confirm by email, which leaves a pending appointment
 * holding its slot for a short while. Nobody confirms it here, so the
 * clinician's calendar shows it as held.
 *
 * Design: docs/design/public-booking.md.
 */

import type { Locator, Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { BROWSER_TIME_ZONE } from "../fixtures/clock"
import {
  giveBookingLink,
  giveWorkingHours,
  markCalendarSetupComplete,
} from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

test.describe.configure({ retries: 0 })

const DAY_MS = 24 * 60 * 60 * 1000

/** A day as the browser sees it: `YYYY-MM-DD`, engine weekday, header label. */
function browserDay(at: Date): { iso: string; engineWeekday: number; header: string } {
  const part = (options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat("en-US", { timeZone: BROWSER_TIME_ZONE, ...options }).format(at)
  const iso = new Intl.DateTimeFormat("en-CA", { timeZone: BROWSER_TIME_ZONE }).format(at)
  const weekday = part({ weekday: "long" })
  const sundayFirst = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
  return {
    iso,
    // Monday-based, matching the engine's `date.weekday()`.
    engineWeekday: (sundayFirst.indexOf(weekday) + 6) % 7,
    header: `Select ${weekday} ${part({ month: "short" })} ${part({ day: "numeric" })}`,
  }
}

/** What the card says about a hold lapsing at `expires`: the time on the
 * same day, otherwise the weekday. Holds from a booking link are short. */
function heldUntil(expires: Date, now: Date): string {
  if (browserDay(expires).iso === browserDay(now).iso) {
    const time = new Intl.DateTimeFormat("en-US", {
      timeZone: BROWSER_TIME_ZONE,
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    }).format(expires)
    return `Held until ${time}`
  }
  const weekday = new Intl.DateTimeFormat("en-US", {
    timeZone: BROWSER_TIME_ZONE,
    weekday: "short",
  }).format(expires)
  return `Held until ${weekday}`
}

/** Step the week view forward until the day's header is on screen. */
async function showWeekOf(page: Page, header: string): Promise<Locator> {
  const cell = page.getByRole("button", { name: header, exact: true })
  for (let step = 0; step < 2 && !(await cell.isVisible()); step++) {
    await page.getByRole("button", { name: "Next", exact: true }).click()
  }
  await expect(cell).toBeVisible()
  return cell
}

interface Appointment {
  id: string
  start_at: string
  status: string
  pending_expires_at: string | null
}

let ruleId: string | null = null
let holdId: string | null = null

test.afterEach(async ({ api }) => {
  if (holdId) await api.delete(`/api/appointments/${holdId}`)
  if (ruleId) await api.delete(`/api/availability/rules/${ruleId}`)
  holdId = null
  ruleId = null
})

test("a held time shows when it lapses, and selecting days opens the range bar", async ({
  signedInPage: page,
  api,
  request,
}) => {
  // Two days out: the booking page starts tomorrow, so this stays off its edge.
  const day = browserDay(new Date(Date.now() + 2 * DAY_MS))
  ruleId = (await giveWorkingHours(api, day.engineWeekday, { start: "09:00", end: "17:00" })).id
  await markCalendarSetupComplete(api)
  const link = await giveBookingLink(api, { title: "Held-time check" })

  const slots = await request.get(
    `${BACKEND_URL}/api/public/booking-links/${link.slug}/slots?date=${day.iso}`,
  )
  expect(slots.ok()).toBe(true)
  // The day's last opening: other booking specs share this practice and take
  // the first ones, so starting from the end keeps the two apart.
  const openings = (await slots.json()).slots as { start: string }[]
  expect(openings.length, "the hours should leave an opening").toBeGreaterThan(0)
  const slot = openings[openings.length - 1]

  const booked = await request.post(`${BACKEND_URL}/api/public/booking-links/${link.slug}/bookings`, {
    data: {
      first_name: "Ellis",
      last_name: "Marlowe",
      email: "ellis.marlowe@example.com",
      start_at: slot.start,
      note: null,
    },
  })
  expect(booked.status(), await booked.text()).toBeLessThan(300)

  // The clinician's own view of it: pending, with the instant it lapses.
  const from = new Date(new Date(slot.start).getTime() - DAY_MS).toISOString()
  const to = new Date(new Date(slot.start).getTime() + DAY_MS).toISOString()
  const listed = await api.get<{ data: Appointment[] }>(
    `/api/appointments?start=${encodeURIComponent(from)}&end=${encodeURIComponent(to)}`,
  )
  const hold = listed.data.find(
    (a) => a.status === "pending" && new Date(a.start_at).getTime() === new Date(slot.start).getTime(),
  )
  expect(hold, "the booking should be on the calendar as a pending hold").toBeDefined()
  holdId = hold!.id
  expect(hold!.pending_expires_at).not.toBeNull()

  await page.goto("/dashboard/calendar")
  await expect(page.getByLabel("Weekly appointment calendar")).toBeVisible()
  const header = await showWeekOf(page, day.header)

  const card = page.getByRole("button", { name: /^Ellis Marlowe at .*Held until/ })
  await expect(card).toBeVisible()
  await expect(card.getByTestId("event-note")).toHaveText(
    heldUntil(new Date(hold!.pending_expires_at!), new Date()),
  )

  // Drag from that day to its neighbour in the same week.
  const headers = page.getByRole("button", { name: /^Select / })
  const labels = await headers.evaluateAll((els) => els.map((el) => el.getAttribute("aria-label")))
  const at = labels.indexOf(day.header)
  const neighbour = headers.nth(at === labels.length - 1 ? at - 1 : at + 1)

  await header.hover()
  await page.mouse.down()
  await neighbour.hover()
  await page.mouse.up()

  const bar = page.getByRole("region", { name: "Selected dates" })
  await expect(bar).toBeVisible()
  await expect(header).toHaveAttribute("aria-pressed", "true")
  await expect(neighbour).toHaveAttribute("aria-pressed", "true")
  await expect(bar).toContainText(" – ")

  await page.keyboard.press("Escape")
  await expect(bar).toHaveCount(0)
})
