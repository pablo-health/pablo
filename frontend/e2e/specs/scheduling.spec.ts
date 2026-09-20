// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { test, expect } from "../fixtures/auth"
import { givePatient, giveWorkingHours, markCalendarSetupComplete } from "../fixtures/scenarios"

/** The slot this test books, and the hours it books inside. */
const BOOKS_AT = "10:00"
const OPENS_AT = "09:00"
const CLOSES_AT = "17:00"

/**
 * The practice's timezone, which this test types wall-clock times in.
 *
 * The booking form sends what the BROWSER makes of "10:00", and the engine
 * asks whether that instant falls inside the practice's working hours, which
 * are practice-local. Leave the browser on the runner's timezone and those
 * two are only the same clock by luck: on a UTC runner "10:00" becomes 06:00
 * for a New York practice — outside every plausible working day, and the
 * booking raises an override dialog that silently swallows the submit.
 *
 * That is why this test passed on a developer's machine in US/Eastern and
 * failed for three minutes on CI. Pinning it here makes the time typed and
 * the time evaluated the same time, whoever runs it.
 *
 * This isn't only a time-of-day problem. The Node process computing
 * `new Date()` runs in UTC on CI, while the browser is pinned to this
 * timezone — so between 00:00 and 04:00 UTC (05:00 during standard time)
 * the two clocks can disagree about the DAY, not just the hour. Every date
 * this spec uses has to be read off the practice's clock, the same way the
 * time-of-day is, or "tomorrow" in Node and "tomorrow" in the browser can
 * name different calendar dates. See `practiceLocalTomorrow` below.
 *
 * Must match the clinician default in `app.models.user`.
 */
const PRACTICE_TIMEZONE = "America/New_York"

test.use({ timezoneId: PRACTICE_TIMEZONE })

/**
 * The practice-local calendar date `instant` falls on, plus that date's day
 * of the week (0 = Sunday, matching JS `Date#getDay()`).
 *
 * `Intl.DateTimeFormat` reads the date the way a wall clock in `timeZone`
 * would, independent of what zone the Node process itself is running in —
 * which is the property `new Date()` alone doesn't have.
 */
function practiceLocalDate(
  instant: Date,
  timeZone: string,
): { year: number; month: number; day: number; weekday: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
  }).formatToParts(instant)
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? ""
  const weekdayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

  return {
    year: Number(part("year")),
    month: Number(part("month")),
    day: Number(part("day")),
    weekday: weekdayNames.indexOf(part("weekday")),
  }
}

/**
 * `practiceLocalDate`, advanced by one practice-local calendar day.
 *
 * Rolls the date forward as a bare y/m/d — via a UTC-midnight `Date` used
 * only as a calendar, never as an instant — rather than adding 24 hours to
 * `instant` and re-reading the zone, so a DST transition on the rolled-over
 * day can't skip or repeat a calendar date.
 */
function practiceLocalTomorrow(
  instant: Date,
  timeZone: string,
): { year: number; month: number; day: number; weekday: number } {
  const today = practiceLocalDate(instant, timeZone)
  const calendar = new Date(Date.UTC(today.year, today.month - 1, today.day))
  calendar.setUTCDate(calendar.getUTCDate() + 1)

  return {
    year: calendar.getUTCFullYear(),
    month: calendar.getUTCMonth() + 1,
    day: calendar.getUTCDate(),
    weekday: calendar.getUTCDay(),
  }
}

test("a clinician books and cancels an appointment", async ({ signedInPage: page, api }) => {
  const patient = await givePatient(api)
  await markCalendarSetupComplete(api)

  const tomorrow = practiceLocalTomorrow(new Date(), PRACTICE_TIMEZONE)

  // Declare the hours this test books within, rather than inheriting whatever
  // a freshly provisioned practice happens to have. Booking outside the
  // practice's working hours is not refused — it raises an override dialog
  // (#1103, "Ask the clinician instead of refusing the booking") that sits on
  // top of this form and swallows the submit. The spec then waits for a POST
  // that will never be made and reports the wait, three minutes later,
  // instead of the dialog that caused it.
  //
  // `day_of_week` is MONDAY-based, matching the engine's `date.weekday()`;
  // JS `getDay()` (and `tomorrow.weekday` above) is Sunday-based. This is
  // the second weekday trap in this test — see the week-view note further
  // down for the first.
  await giveWorkingHours(api, (tomorrow.weekday + 6) % 7, {
    start: OPENS_AT,
    end: CLOSES_AT,
  })

  await page.goto("/dashboard/calendar")
  await page.getByRole("button", { name: /new appointment/i }).click()

  const patientPicker = page.getByRole("combobox", { name: "Patient" })
  await patientPicker.click()
  await patientPicker.fill(patient.last_name)
  await page
    .getByRole("option", { name: `${patient.last_name}, ${patient.first_name}` })
    .click()

  await page
    .getByLabel("Date", { exact: true })
    .fill(
      `${tomorrow.year}-${String(tomorrow.month).padStart(2, "0")}-${String(tomorrow.day).padStart(2, "0")}`,
    )
  await page.getByLabel("Time", { exact: true }).fill(BOOKS_AT)

  const created = page.waitForResponse(
    (response) =>
      response.url().includes("/api/appointments") &&
      response.request().method() === "POST" &&
      response.ok(),
  )
  await page.getByRole("button", { name: "Schedule", exact: true }).click()
  const appointment = (await (await created).json()) as { id: string }

  try {
    // The week view runs Sunday to Saturday (`weekStartsOn: 0` in
    // editorial/dateUtils), so booking for tomorrow from a SATURDAY puts the
    // appointment in next week, where the default view cannot see it. The
    // appointment is created either way — it is simply off-screen — so the
    // assertion below would fail one day in seven, which is exactly how this
    // went unnoticed until a Saturday CI run.
    if (tomorrow.weekday === 0) {
      await page.getByRole("button", { name: "Next", exact: true }).click()
    }
    await expect(page.getByText(`${patient.first_name} ${patient.last_name}`, { exact: true })).toBeVisible()
    const session = await api.post<{ id: string }>(
      `/api/appointments/${appointment.id}/start-session`,
    )
    expect(session.id).toBeTruthy()

    await page.getByText(`${patient.first_name} ${patient.last_name}`, { exact: true }).click()
    await page.getByRole("button", { name: "Edit", exact: true }).click()
    await page.getByRole("button", { name: "Cancel appointment", exact: true }).click()
    await expect(
      page.getByText(`${patient.first_name} ${patient.last_name}`, { exact: true }),
    ).toBeHidden()
  } finally {
    await api.delete(`/api/appointments/${appointment.id}`)
  }
})
