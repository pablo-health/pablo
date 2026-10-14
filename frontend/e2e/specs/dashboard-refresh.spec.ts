// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * An appointment booked on the calendar is on the dashboard when the
 * clinician goes back to it, without a reload.
 *
 * The dashboard is one cached read that is kept for a minute and not
 * refetched on focus. Booking from the calendar used to refresh only the
 * calendar's own lists, so going straight back to the dashboard showed the
 * day as it was before the booking. This walks that path inside the minute,
 * moving between pages by their links so the cache is the one the app keeps.
 */

import { test, expect } from "../fixtures/auth"
import { BROWSER_TIME_ZONE } from "../fixtures/clock"
import { givePatient, giveWorkingHours, markCalendarSetupComplete } from "../fixtures/scenarios"

const OPENS_AT = "09:00"
const CLOSES_AT = "17:00"

/**
 * Today on the browser's calendar, read the way a wall clock there would.
 *
 * The Node process running this spec is on UTC, the browser is not, and for
 * a few hours every evening the two disagree about the date. The booking form
 * takes a date the browser reads in its own zone, so the date typed has to
 * come from that zone too.
 */
function browserToday(): { iso: string; weekday: number } {
  const now = new Date()
  const iso = new Intl.DateTimeFormat("en-CA", { timeZone: BROWSER_TIME_ZONE }).format(now)
  const weekdayName = new Intl.DateTimeFormat("en-US", {
    timeZone: BROWSER_TIME_ZONE,
    weekday: "short",
  }).format(now)
  return { iso, weekday: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(weekdayName) }
}

test("an appointment booked from the calendar is on the dashboard without a reload", async ({
  signedInPage: page,
  api,
}) => {
  const patient = await givePatient(api)
  const name = `${patient.first_name} ${patient.last_name}`
  await markCalendarSetupComplete(api)

  // Today lists every session on the day, earlier ones included, so the time
  // only has to fall on today's date. A fixed window inside the working hours
  // declared here keeps the booking clear of the out-of-hours dialog and of
  // midnight, whatever the hour the suite runs; a random five minutes in it
  // keeps reruns from booking over each other.
  const today = browserToday()
  // `day_of_week` is Monday-based; JS weekdays are Sunday-based.
  const hours = await giveWorkingHours(api, (today.weekday + 6) % 7, {
    start: OPENS_AT,
    end: CLOSES_AT,
  })
  const slot = Math.floor(Math.random() * 60)
  const bookAt = `${String(10 + Math.floor(slot / 12)).padStart(2, "0")}:${String((slot % 12) * 5).padStart(2, "0")}`
  let appointmentId: string | undefined

  // Both go again afterwards. Hours left on today's weekday refuse what a
  // later spec books outside them today, and only a new worker clears them.
  try {
    const summaryLoaded = page.waitForResponse(
      (response) => response.url().includes("/api/dashboard/summary") && response.ok(),
    )
    await page.goto("/dashboard")
    await summaryLoaded
    await expect(page.getByRole("heading", { name: "Today" })).toBeVisible()
    await expect(page.getByRole("listitem").filter({ hasText: name })).toHaveCount(0)
    // Anything below that reloads the page would hide the bug; this marker
    // only survives client-side navigation.
    await page.evaluate(() => {
      ;(window as unknown as { __sameDocument?: boolean }).__sameDocument = true
    })

    await page.getByRole("link", { name: "Open calendar" }).click()
    await page.getByRole("button", { name: /new appointment/i }).click()

    const patientPicker = page.getByRole("combobox", { name: "Client" })
    await patientPicker.click()
    await patientPicker.fill(patient.first_name)
    await page.getByRole("option", { name: `${patient.last_name}, ${patient.first_name}` }).click()
    await page.getByLabel("Date", { exact: true }).fill(today.iso)
    await page.getByLabel("Time", { exact: true }).fill(bookAt)

    const created = page.waitForResponse(
      (response) =>
        response.url().includes("/api/appointments") &&
        response.request().method() === "POST" &&
        response.ok(),
    )
    await page.getByRole("button", { name: "Schedule", exact: true }).click()
    appointmentId = ((await (await created).json()) as { id: string }).id

    await page
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("link", { name: "Dashboard", exact: true })
      .click()
    await expect(page).toHaveURL(/\/dashboard$/)

    await expect(page.getByRole("listitem").filter({ hasText: name })).toBeVisible()
    expect(
      await page.evaluate(
        () => (window as unknown as { __sameDocument?: boolean }).__sameDocument === true,
      ),
    ).toBe(true)
  } finally {
    if (appointmentId) await api.delete(`/api/appointments/${appointmentId}`)
    await api.delete(`/api/availability/rules/${hours.id}`)
  }
})
