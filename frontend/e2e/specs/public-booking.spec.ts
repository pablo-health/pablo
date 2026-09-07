// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The anonymous booking link, end to end: a stranger with the URL picks a
 * time, books it, confirms by email and cancels — and the appointment lands
 * on the clinician's calendar.
 *
 * This is the only internet-facing write path in the product, so the spec
 * checks what it refuses as well as what it accepts: the held slot is gone
 * for the next visitor, a second booking of it is refused, the cancel link
 * works exactly once, and nothing the anonymous surface returns carries a
 * field about a person.
 *
 * Three tests in order, sharing one link and one booking, because the
 * write surface is rate-limited: ten requests an hour per address, counting
 * the booking POST, a refused one, and the cancel alike. One run spends
 * three of those, which is free in CI (a run gets a stack of its own and
 * the window is held in memory) and means about three runs an hour against
 * a stack left up — `make e2e-down && make e2e-up` clears the window when
 * iterating. (Restarting the API alone does not: the frontend shares its
 * network namespace and loses the host port with it.)
 *
 * Design: docs/design/public-booking.md, docs/design/e2e-harness.md.
 */

import type { APIResponse, Browser, BrowserContext, Page } from "@playwright/test"
import type { ApiClient } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"
import { firstLink, mail } from "../fixtures/mail"
import { giveBookingLink, giveWorkingHours, type BookingLink } from "../fixtures/scenarios"
import { BACKEND_URL, BASE_URL } from "../fixtures/stack"

/** Monday-based, matching the engine's `date.weekday()`. */
const BOOKABLE_WEEKDAY = 2 // Wednesday
const OPENS_AT = "09:00"
const CLOSES_AT = "17:00"

const BOOKER = {
  firstName: "Rowan",
  lastName: "Ellery",
  email: "rowan.ellery@example.com",
}

/**
 * The next `BOOKABLE_WEEKDAY` at least two days out, as `YYYY-MM-DD`.
 *
 * The picker starts tomorrow and shows a fortnight, so any weekday from two
 * days out is on it; "at least two" keeps the choice off the edge the
 * page's own "start tomorrow" rule sits on.
 */
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

/** The picker's own label for a date: "Wed 10 Sep". */
function dayPickerLabel(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number)
  const when = new Date(year, month - 1, day)
  const weekday = when.toLocaleDateString("en-US", { weekday: "short" })
  return `${weekday} ${day} ${when.toLocaleDateString("en-US", { month: "short" })}`
}

/** A browser with nothing saved in it: the stranger holding the link. */
async function anonymousPage(browser: Browser): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({ baseURL: BASE_URL, storageState: undefined })
  return { context, page: await context.newPage() }
}

/**
 * Every field the anonymous surface is allowed to return. A response that
 * grows a key not on this list has started disclosing something, which is
 * the failure this assertion exists to catch.
 */
const CARD_FIELDS = new Set([
  "slug",
  "host_name",
  "title",
  "description",
  "duration_minutes",
  "captcha_site_key",
])
const SLOTS_FIELDS = new Set(["date", "duration_minutes", "slots", "total", "configured"])
const SLOT_FIELDS = new Set(["start", "end"])

function expectOnlyFields(payload: unknown, allowed: Set<string>, what: string): void {
  const extra = Object.keys(payload as Record<string, unknown>).filter((k) => !allowed.has(k))
  expect(extra, `${what} returned fields the public surface must not disclose`).toEqual([])
}

const bookableDate = nextBookableDate()

let link: BookingLink
/** The slot the booking is made against, as the API renders it. */
let bookedStartAt: string
/** The capability token out of the confirmation email. */
let bookingToken: string

test.describe.configure({ mode: "serial" })

test.describe("Public booking link", () => {
  test("a stranger books a slot and is asked to confirm by email", async ({ api, browser }) => {
    await mail.reset()
    await giveWorkingHours(api, BOOKABLE_WEEKDAY, { start: OPENS_AT, end: CLOSES_AT })
    link = await giveBookingLink(api, { host_name: "Wren Calloway", title: "Intake call" })

    const { context, page } = await anonymousPage(browser)
    try {
      const cardResponse = page.waitForResponse(
        (r) => r.url().endsWith(`/api/public/booking-links/${link.slug}`) && r.ok(),
      )
      const slotsResponse = page.waitForResponse(
        (r) => r.url().includes(`/slots?date=${bookableDate}`) && r.ok(),
      )

      await page.goto(`/book/${link.slug}`)
      await expect(page.getByRole("heading", { name: "Intake call" })).toBeVisible()
      await expect(page.getByText("Wren Calloway").first()).toBeVisible()
      await expect(page.getByText(/50 minutes/)).toBeVisible()

      expectOnlyFields(await (await cardResponse).json(), CARD_FIELDS, "the booking card")

      // Pick the day the clinician actually works, then its first opening.
      await page.getByRole("button", { name: dayPickerLabel(bookableDate), exact: true }).click()

      const slots = await (await slotsResponse).json()
      expectOnlyFields(slots, SLOTS_FIELDS, "the slot list")
      expect(slots.configured, "the clinician's working hours should be on file").toBe(true)
      expect(slots.slots.length).toBeGreaterThan(0)
      for (const slot of slots.slots) expectOnlyFields(slot, SLOT_FIELDS, "a slot")
      bookedStartAt = slots.slots[0].start

      await page.getByRole("button", { name: "9:00 AM" }).click()
      await page.getByLabel("First name").fill(BOOKER.firstName)
      await page.getByLabel("Last name").fill(BOOKER.lastName)
      await page.getByLabel("Email").fill(BOOKER.email)
      await page.getByRole("button", { name: "Confirm booking" }).click()

      // A link is born requiring the booker to prove the address, so this
      // is a hold, not yet an appointment anyone should rely on.
      await expect(page.getByRole("heading", { name: "Almost there" })).toBeVisible()
      await expect(page.getByText(/Check your email to confirm/)).toBeVisible()
    } finally {
      await context.close()
    }

    const message = await mail.waitFor(BOOKER.email)
    expect(message.subject).toContain("Intake call")
    expect(message.subject).toContain("Wren Calloway")
    const confirmUrl = new URL(firstLink(message))
    expect(confirmUrl.pathname).toBe(`/book/${link.slug}/confirm`)
    bookingToken = confirmUrl.searchParams.get("token") ?? ""
    expect(bookingToken).not.toBe("")
  })

  test("the held slot is refused to the next booker", async ({ browser, request }) => {
    // The hold is an appointment like any other, so the engine takes the
    // slot off everyone else's list the moment it is placed.
    const { context, page } = await anonymousPage(browser)
    try {
      const slotsResponse = page.waitForResponse(
        (r) => r.url().includes(`/slots?date=${bookableDate}`) && r.ok(),
      )
      await page.goto(`/book/${link.slug}`)
      await page.getByRole("button", { name: dayPickerLabel(bookableDate), exact: true }).click()
      const slots = await (await slotsResponse).json()
      expect(
        slots.slots.some((s: { start: string }) => s.start === bookedStartAt),
        "the held slot should be gone from the public list",
      ).toBe(false)
      await expect(page.getByRole("button", { name: "9:00 AM" })).toHaveCount(0)
    } finally {
      await context.close()
    }

    // And the server refuses it even when asked for directly: the client is
    // never trusted about availability.
    const refused: APIResponse = await request.post(
      `${BACKEND_URL}/api/public/booking-links/${link.slug}/bookings`,
      {
        data: {
          first_name: "Sasha",
          last_name: "Peralta",
          email: "sasha.peralta@example.com",
          start_at: bookedStartAt,
          note: null,
        },
      },
    )
    expect(refused.status()).toBe(409)
  })

  test("the emailed link confirms the booking, and cancels it once", async ({
    api,
    browser,
    request,
  }) => {
    const { context, page } = await anonymousPage(browser)
    try {
      await page.goto(`/book/${link.slug}/confirm?token=${bookingToken}`)
      await expect(page.getByRole("heading", { name: "You're booked" })).toBeVisible()
      await expect(page.getByText("Intake call with Wren Calloway")).toBeVisible()

      // The clinician's own calendar, read as the clinician: the booking is
      // an ordinary appointment on it, under the client's name.
      const booked = await onCalendar(api, bookedStartAt)
      expect(booked).not.toBeUndefined()
      expect(booked!.status).toBe("confirmed")
      expect(booked!.title).toBe("Intake call")
      expect(booked!.patient_name).toBe(`${BOOKER.firstName} ${BOOKER.lastName}`)

      // The same token, folded into the manage URL, is the booker's own way
      // out — see docs/design/public-booking.md.
      await page.goto(`/book/${link.slug}/manage?token=${bookingToken}`)
      await expect(page.getByRole("heading", { name: /Intake call with Wren Calloway/ })).toBeVisible()
      await page.getByRole("button", { name: "Cancel appointment" }).click()
      await page.getByRole("button", { name: "Yes, cancel it" }).click()
      await expect(page.getByRole("heading", { name: "Appointment cancelled" })).toBeVisible()
    } finally {
      await context.close()
    }

    // Once: the token is spent, and the clinician's calendar agrees.
    const second = await request.post(
      `${BACKEND_URL}/api/public/booking-links/${link.slug}/manage/cancel`,
      { data: { token: bookingToken } },
    )
    expect(second.status()).toBe(404)

    const after = await onCalendar(api, bookedStartAt)
    expect(after === undefined || after.status === "cancelled").toBe(true)
  })
})

interface CalendarAppointment {
  start_at: string
  status: string
  title: string
  patient_name: string | null
}

/** The clinician's appointment starting at `startAt`, if there is one. */
async function onCalendar(
  api: ApiClient,
  startAt: string,
): Promise<CalendarAppointment | undefined> {
  const day = startAt.slice(0, 10)
  const listed = await api.get<{ data: CalendarAppointment[] }>(
    `/api/appointments?start=${day}T00:00:00Z&end=${day}T23:59:59Z`,
  )
  return listed.data.find((a) => a.start_at.slice(0, 16) === startAt.slice(0, 16))
}
