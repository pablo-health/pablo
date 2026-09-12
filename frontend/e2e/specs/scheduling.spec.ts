// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { test, expect } from "../fixtures/auth"
import { givePatient, markCalendarSetupComplete } from "../fixtures/scenarios"

test("a clinician books and cancels an appointment", async ({ signedInPage: page, api }) => {
  const patient = await givePatient(api)
  await markCalendarSetupComplete(api)
  await page.goto("/dashboard/calendar")
  await page.getByRole("button", { name: /new appointment/i }).click()

  const patientPicker = page.getByRole("combobox", { name: "Patient" })
  await patientPicker.click()
  await patientPicker.fill(patient.last_name)
  await page
    .getByRole("option", { name: `${patient.last_name}, ${patient.first_name}` })
    .click()

  const tomorrow = new Date()
  tomorrow.setDate(tomorrow.getDate() + 1)
  await page
    .getByLabel("Date", { exact: true })
    .fill(
      `${tomorrow.getFullYear()}-${String(tomorrow.getMonth() + 1).padStart(2, "0")}-${String(tomorrow.getDate()).padStart(2, "0")}`,
    )
  await page.getByLabel("Time", { exact: true }).fill("10:00")

  const created = page.waitForResponse(
    (response) =>
      response.url().includes("/api/appointments") &&
      response.request().method() === "POST" &&
      response.ok(),
  )
  await page.getByRole("button", { name: "Schedule", exact: true }).click()
  const appointment = (await (await created).json()) as { id: string }

  try {
    // The week grid starts on Sunday and always opens on today's week, so
    // when today is Saturday tomorrow's appointment belongs to the next one
    // and the default view never shows it. Step forward on that one day.
    if (tomorrow.getDay() === 0) {
      await page.getByRole("button", { name: "Next" }).click()
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
