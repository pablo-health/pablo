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
 * Must match the clinician default in `app.models.user`.
 */
const PRACTICE_TIMEZONE = "America/New_York"

test.use({ timezoneId: PRACTICE_TIMEZONE })

test("a clinician books and cancels an appointment", async ({ signedInPage: page, api }) => {
  const patient = await givePatient(api)
  await markCalendarSetupComplete(api)

  const tomorrow = new Date()
  tomorrow.setDate(tomorrow.getDate() + 1)

  // Declare the hours this test books within, rather than inheriting whatever
  // a freshly provisioned practice happens to have. Booking outside the
  // practice's working hours is not refused — it raises an override dialog
  // (#1103, "Ask the clinician instead of refusing the booking") that sits on
  // top of this form and swallows the submit. The spec then waits for a POST
  // that will never be made and reports the wait, three minutes later,
  // instead of the dialog that caused it.
  //
  // `day_of_week` is MONDAY-based, matching the engine's `date.weekday()`;
  // JS `getDay()` is Sunday-based. This is the second weekday trap in this
  // test — see the week-view note further down for the first.
  await giveWorkingHours(api, (tomorrow.getDay() + 6) % 7, {
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
      `${tomorrow.getFullYear()}-${String(tomorrow.getMonth() + 1).padStart(2, "0")}-${String(tomorrow.getDate()).padStart(2, "0")}`,
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
    if (tomorrow.getDay() === 0) {
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
