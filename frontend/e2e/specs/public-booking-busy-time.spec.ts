// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Busy time on the clinician's own calendar keeps a time off their booking
 * page.
 *
 * The page is loaded once before Google is connected, as the control: the
 * time is offered, so its absence afterwards is the calendar's doing and not
 * the hours'. Then an event goes on the stand-in's main calendar over exactly
 * that time, Google is connected through the real setup page (busy times are
 * asked for by default), and the same page no longer offers it while the
 * time before it still is.
 *
 * Reads only. The booking POST is rate-limited per address and the
 * public-booking spec spends that budget; that a busy time is also refused
 * when posted directly is proved by the route tests.
 */

import type { Browser, BrowserContext, Page } from "@playwright/test"
import { ApiClient, ApiError } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"
import { SCOPE_FREEBUSY, google } from "../fixtures/google"
import { giveBookingLink, giveWorkingHours, type BookingLink } from "../fixtures/scenarios"
import { BASE_URL } from "../fixtures/stack"

const SETUP_PATH = "/dashboard/settings/calendar"

/** Monday-based, matching the engine's `date.weekday()`. */
const BOOKABLE_WEEKDAY = 3 // Thursday

interface Slot {
  start: string
  end: string
}

/** The next `BOOKABLE_WEEKDAY` at least two days out, as `YYYY-MM-DD`. */
function nextBookableDate(): string {
  const day = new Date()
  day.setDate(day.getDate() + 2)
  while ((day.getDay() + 6) % 7 !== BOOKABLE_WEEKDAY) {
    day.setDate(day.getDate() + 1)
  }
  const month = String(day.getMonth() + 1).padStart(2, "0")
  const date = String(day.getDate()).padStart(2, "0")
  return `${day.getFullYear()}-${month}-${date}`
}

/** The picker's own label for a date: "Thu 11 Sep". */
function dayPickerLabel(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number)
  const when = new Date(year, month - 1, day)
  const weekday = when.toLocaleDateString("en-US", { weekday: "short" })
  return `${weekday} ${day} ${when.toLocaleDateString("en-US", { month: "short" })}`
}

/** The page's own label for a slot, which it renders off the slot's clock: "9:50 AM". */
function slotLabel(start: string): string {
  const hours = Number(start.slice(11, 13))
  const minutes = start.slice(14, 16)
  return `${hours % 12 || 12}:${minutes} ${hours < 12 ? "AM" : "PM"}`
}

async function anonymousPage(browser: Browser): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({ baseURL: BASE_URL, storageState: undefined })
  return { context, page: await context.newPage() }
}

/** Open the booking page as a stranger, pick the day, and return what it was offered. */
async function offered(browser: Browser, slug: string, date: string): Promise<{
  slots: Slot[]
  context: BrowserContext
  page: Page
}> {
  const { context, page } = await anonymousPage(browser)
  const response = page.waitForResponse((r) => r.url().includes(`/slots?date=${date}`) && r.ok())
  await page.goto(`/book/${slug}`)
  await page.getByRole("button", { name: dayPickerLabel(date), exact: true }).click()
  const body = (await (await response).json()) as { slots: Slot[] }
  return { slots: body.slots, context, page }
}

async function disconnectGoogle(api: ApiClient): Promise<void> {
  try {
    await api.delete("/api/google-calendar/disconnect")
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) throw error
  }
}

let addedRule: string | null = null

test.afterEach(async ({ api }) => {
  // The worker's diary and connection are shared with every later spec.
  await disconnectGoogle(api)
  await google.reset(`after-busy-${Date.now()}@example.test`)
  if (addedRule) await api.delete(`/api/availability/rules/${addedRule}`)
  addedRule = null
})

test("a busy event on the clinician's calendar takes that time off the booking page", async ({
  signedInPage: page,
  api,
  browser,
}) => {
  await disconnectGoogle(api)
  await google.reset(`busy-${Date.now()}@example.test`)
  addedRule = (await giveWorkingHours(api, BOOKABLE_WEEKDAY, { start: "09:00", end: "17:00" })).id
  const link: BookingLink = await giveBookingLink(api, { title: "Busy-time check" })
  const date = nextBookableDate()

  // Control: with no calendar connected, the time is offered.
  const before = await offered(browser, link.slug, date)
  const [kept, busy] = before.slots
  try {
    expect(busy, "the hours should leave more than one opening").toBeDefined()
    await expect(before.page.getByRole("button", { name: slotLabel(busy.start) })).toBeVisible()
  } finally {
    await before.context.close()
  }

  // Something else on the clinician's calendar, over exactly that time.
  await google.seed("primary", {
    summary: "Dentist",
    start: { dateTime: busy.start },
    end: { dateTime: busy.end },
  })

  await page.goto(SETUP_PATH)
  await page.getByRole("button", { name: "Continue with Google" }).click()
  await expect(page.getByText("Google Calendar is connected.")).toBeVisible()
  expect(await google.grant()).toContain(SCOPE_FREEBUSY)

  const after = await offered(browser, link.slug, date)
  try {
    const starts = after.slots.map((slot) => slot.start)
    expect(starts, "the busy time is still offered").not.toContain(busy.start)
    expect(starts, "the time before it should be untouched").toContain(kept.start)
    await expect(after.page.getByRole("button", { name: slotLabel(busy.start) })).toHaveCount(0)
    await expect(after.page.getByRole("button", { name: slotLabel(kept.start) })).toBeVisible()
  } finally {
    await after.context.close()
  }
})
