// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Time kept free inside a window: an hour for lunch somewhere between 11:30
 * and 2, wherever the day leaves room.
 *
 * The practice works 9 to 5 on the chosen weekday and keeps lunch floating.
 * A session booked 11:30 to 12:15 pushes lunch to 12:15, and the calendar
 * draws it there. A 50-minute booking that starts between 12:15 and 1:15
 * would leave no free hour before 2, so a client booking through the portal
 * is offered nothing that overlaps lunch, while 1:15 (which leaves the hour
 * before it) and the afternoon stay open. Skipping lunch for that day takes
 * the band off the calendar and, as the control that makes the refusal mean
 * something, brings those times back onto the portal's list. Putting it back
 * redraws the band.
 *
 * The portal's slot list is read through its API with a redeemed session
 * rather than walked in the portal's picker: what is under test is which
 * times the engine offers, and portal-appointments.spec.ts already drives
 * the picker itself. Reads only; that a booking taking the last free stretch
 * is refused is proved by the engine and route tests.
 */

import type { APIRequestContext } from "@playwright/test"
import type { ApiClient } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"
import { givePortalContactDetails, givePortalSession } from "../fixtures/portal"
import { letExistingClientsSelfBook } from "../fixtures/portal-appointments"
import {
  giveAvailabilityRule,
  givePatient,
  giveWorkingHours,
  markCalendarSetupComplete,
  type AvailabilityRule,
} from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

/**
 * The practice's zone, and the browser's. The clinician default in
 * `app.models.user`; with both on one clock, "12:15" on the calendar and
 * "12:15" in the window are the same time. See scheduling.spec.ts.
 */
const PRACTICE_TIMEZONE = "America/New_York"

test.use({ timezoneId: PRACTICE_TIMEZONE })

/** Monday-based, matching the engine's `date.weekday()`. Its own weekday,
 * so the hours here do not stack on a sibling's. */
const WEEKDAY = 1 // Tuesday
const OPENS_AT = "09:00"
const CLOSES_AT = "17:00"
const LUNCH = {
  label: "Lunch",
  duration_minutes: 60,
  window_start: "11:30",
  window_end: "14:00",
}
const SESSION = { start: "11:30", end: "12:15" }
/** What the portal offers a client, in minutes. */
const OFFERED_MINUTES = 50

interface CalendarDate {
  year: number
  month: number
  day: number
}

interface Slot {
  start_at: string
  end_at: string
}

interface FloatingBlocks {
  blocks: { label: string; state: string; start: string | null }[]
}

/** Today on the practice's clock, whatever zone Node runs in. */
function practiceToday(): CalendarDate {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: PRACTICE_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date())
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  return { year: part("year"), month: part("month"), day: part("day") }
}

/** A calendar date as a UTC-midnight Date, used only as a calendar. */
function asCalendar(date: CalendarDate): Date {
  return new Date(Date.UTC(date.year, date.month - 1, date.day))
}

function fromCalendar(calendar: Date): CalendarDate {
  return {
    year: calendar.getUTCFullYear(),
    month: calendar.getUTCMonth() + 1,
    day: calendar.getUTCDate(),
  }
}

/**
 * The next `WEEKDAY` at least three days out. Far enough that no part of it
 * is ever in the past or inside the practice's notice period, near enough
 * to sit inside its booking horizon.
 */
function targetDate(): CalendarDate {
  const calendar = asCalendar(practiceToday())
  calendar.setUTCDate(calendar.getUTCDate() + 3)
  while ((calendar.getUTCDay() + 6) % 7 !== WEEKDAY) {
    calendar.setUTCDate(calendar.getUTCDate() + 1)
  }
  return fromCalendar(calendar)
}

function isoDate(date: CalendarDate): string {
  return `${date.year}-${String(date.month).padStart(2, "0")}-${String(date.day).padStart(2, "0")}`
}

/** How many weeks the calendar must step forward to show `date`. Weeks run
 * Sunday to Saturday (`weekStartsOn: 0` in editorial/dateUtils). */
function weeksAhead(date: CalendarDate): number {
  const sunday = (calendar: Date) => {
    const start = new Date(calendar)
    start.setUTCDate(start.getUTCDate() - start.getUTCDay())
    return start.getTime()
  }
  const week = 7 * 24 * 60 * 60 * 1000
  return Math.round((sunday(asCalendar(date)) - sunday(asCalendar(practiceToday()))) / week)
}

/** The instant the practice's wall clock reads `clock` on `date`. */
function practiceInstant(date: CalendarDate, clock: string): string {
  const [hours, minutes] = clock.split(":").map(Number)
  const wall = Date.UTC(date.year, date.month - 1, date.day, hours, minutes)
  const offsetAt = (instant: number) => {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: PRACTICE_TIMEZONE,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
    }).formatToParts(new Date(instant))
    const part = (type: string) => Number(parts.find((p) => p.type === type)?.value)
    const asUtc = Date.UTC(
      part("year"),
      part("month") - 1,
      part("day"),
      part("hour"),
      part("minute"),
    )
    return asUtc - instant
  }
  const first = wall - offsetAt(wall)
  return new Date(wall - offsetAt(first)).toISOString()
}

/** Minutes since midnight on the practice's clock, for an instant. */
function practiceMinutes(iso: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: PRACTICE_TIMEZONE,
    hourCycle: "h23",
    hour: "numeric",
    minute: "numeric",
  }).formatToParts(new Date(iso))
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  return part("hour") * 60 + part("minute")
}

function clockMinutes(clock: string): number {
  const [hours, minutes] = clock.split(":").map(Number)
  return hours * 60 + minutes
}

/** What the portal offers this client on `date`. */
async function offered(
  request: APIRequestContext,
  sessionToken: string,
  date: CalendarDate,
): Promise<Slot[]> {
  const response = await request.get(
    `${BACKEND_URL}/api/patient/booking/slots?date=${isoDate(date)}&duration_minutes=${OFFERED_MINUTES}`,
    { headers: { Authorization: `Bearer ${sessionToken}` } },
  )
  expect(response.status(), await response.text()).toBe(200)
  return ((await response.json()) as { data: Slot[] }).data
}

/** Lunch's hour once the session has pushed it along: 12:15 to 1:15. */
const LUNCH_SITS = {
  start: clockMinutes(SESSION.end),
  end: clockMinutes(SESSION.end) + LUNCH.duration_minutes,
}

const overlapsLunch = (slot: Slot) =>
  practiceMinutes(slot.start_at) < LUNCH_SITS.end &&
  practiceMinutes(slot.end_at) > LUNCH_SITS.start

let cleanup: (() => Promise<void>) | null = null

test.afterEach(async () => {
  // The worker's rules and diary are shared with every later spec.
  if (cleanup) await cleanup()
  cleanup = null
})

test("lunch floats past a session, is kept off the portal's openings, and can be skipped", async ({
  signedInPage: page,
  api,
  request,
}) => {
  const date = targetDate()
  const day = isoDate(date)
  const made: AvailabilityRule[] = []
  let appointmentId: string | null = null
  cleanup = async () => {
    if (appointmentId) await api.delete(`/api/appointments/${appointmentId}`)
    for (const rule of made.reverse()) await api.delete(`/api/availability/rules/${rule.id}`)
  }

  await markCalendarSetupComplete(api)
  made.push(await giveWorkingHours(api, WEEKDAY, { start: OPENS_AT, end: CLOSES_AT }))
  made.push(
    await giveAvailabilityRule(api, "floating_block", { ...LUNCH, days_of_week: [WEEKDAY] }),
  )
  await letExistingClientsSelfBook(api)
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone, date_of_birth: "1988-04-02" })
  appointmentId = await bookSession(api, patient.id, date)

  // The engine's own answer, so a wrong band below says which side is wrong.
  const resolved = await api.get<FloatingBlocks>(
    `/api/availability/floating-blocks?date=${day}`,
  )
  expect(resolved.blocks).toEqual([expect.objectContaining({ label: "Lunch", state: "floating" })])
  expect(new Date(resolved.blocks[0].start ?? 0).toISOString()).toBe(
    practiceInstant(date, SESSION.end),
  )

  // Nothing that would leave less than the hour; the rest of the window and
  // the afternoon still open.
  const session = await givePortalSession(api, request, patient.id, email, phone)
  const slots = await offered(request, session, date)
  expect(slots.filter(overlapsLunch), "a time over lunch was offered").toEqual([])
  expect(
    slots.some(
      (slot) =>
        practiceMinutes(slot.start_at) >= LUNCH_SITS.end &&
        practiceMinutes(slot.start_at) < clockMinutes(LUNCH.window_end),
    ),
    "a time after lunch, still inside the window, should be offered",
  ).toBe(true)
  expect(
    slots.some((slot) => practiceMinutes(slot.start_at) >= clockMinutes(LUNCH.window_end)),
    "the afternoon should still be offered",
  ).toBe(true)

  // The calendar draws lunch where it settled.
  await page.goto("/dashboard/calendar")
  await page.getByRole("tab", { name: /^week$/i }).click()
  for (let step = 0; step < weeksAhead(date); step++) {
    await page.getByRole("button", { name: "Next", exact: true }).click()
  }
  const band = page.getByRole("button", { name: "Lunch, 12:15 PM to 1:15 PM" })
  await expect(band).toBeVisible()

  // Skipped for the day: the band goes, and the window is bookable again.
  const skipped = page.waitForResponse(
    (r) =>
      r.request().method() === "PUT" && r.url().includes(`/exceptions/${day}`),
  )
  await band.click()
  await page.getByRole("button", { name: "Skip this day" }).click()
  expect((await skipped).status()).toBe(200)
  await expect(page.getByTestId("floating-block")).toHaveCount(0)

  const afterSkip = await offered(request, session, date)
  expect(
    afterSkip.filter(overlapsLunch).length,
    "with lunch skipped its hour should be bookable",
  ).toBeGreaterThan(0)

  // And put back.
  await page.getByRole("button", { name: "Put back Lunch" }).click()
  await expect(band).toBeVisible()
})

/** A session on `date` from SESSION.start to SESSION.end, practice time. */
async function bookSession(
  api: ApiClient,
  patientId: string,
  date: CalendarDate,
): Promise<string> {
  const created = await api.post<{ id: string }>("/api/appointments", {
    patient_id: patientId,
    title: "Session",
    start_at: practiceInstant(date, SESSION.start),
    end_at: practiceInstant(date, SESSION.end),
    duration_minutes: clockMinutes(SESSION.end) - clockMinutes(SESSION.start),
    session_type: "individual",
  })
  return created.id
}
