// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Note types, end to end: start from the psychiatric follow-up
 * template, try a draft on its sample visit, save it, then book an
 * appointment that uses it — through the same note-type picker and
 * appointment details a clinician fills in.
 *
 * The stack drafts through its stand-in (NOTE_GENERATION_BASE_URL), which
 * answers every field with "Stand-in draft for <section>.<field>.", so the
 * draft proves the template's own shape reached the model without one.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient, giveWorkingHours, markCalendarSetupComplete } from "../fixtures/scenarios"

const TEMPLATE_LABEL = "Psychiatric follow-up (E/M + psychotherapy)"
/** Named apart from the built-in it adjusts, which sits in the same picker. */
const TYPE_LABEL = "Medication follow-up"
const BOOKS_AT = "10:00"

/** Pinned so the time typed and the practice's working hours agree; see scheduling.spec.ts. */
const PRACTICE_TIMEZONE = "America/New_York"
test.use({ timezoneId: PRACTICE_TIMEZONE })

/** Tomorrow on the practice's clock, as the date input takes it, plus its Monday-based weekday. */
function practiceTomorrow(): { iso: string; mondayWeekday: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: PRACTICE_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date())
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  const day = new Date(Date.UTC(part("year"), part("month") - 1, part("day") + 1))
  return { iso: day.toISOString().slice(0, 10), mondayWeekday: (day.getUTCDay() + 6) % 7 }
}

type Appointment = { id: string; note_type: string; note_inputs: Record<string, string> | null }
type Session = { note: { note_type: string } | null }

test("a practice starts a note type from a template, tries it, saves it and books with it", async ({
  signedInPage: page,
  api,
}) => {
  let slug: string | null = null
  let appointmentId: string | null = null
  let hoursId: string | null = null
  try {
    await page.goto("/dashboard/settings/note-types")
    await page.getByRole("button", { name: `Start from ${TEMPLATE_LABEL}` }).click()
    await expect(page.getByLabel("Note type name")).toHaveValue(TEMPLATE_LABEL)

    // Try it on the template's sample visit. Place of service is required.
    await expect(page.getByRole("radio", { name: "Sample visit" })).toHaveAttribute("aria-checked", "true")
    await page.getByLabel(/^Place of service/).selectOption("Telehealth")
    const previewed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/note-types/preview" && r.request().method() === "POST",
    )
    await page.getByRole("button", { name: "Draft a note" }).click()
    expect((await previewed).status()).toBe(200)
    const draft = page.getByTestId("try-it-draft")
    await expect(draft.getByText("Stand-in draft for subjective.chief_complaint.")).toBeVisible()
    await expect(draft.getByText("Stand-in draft for plan.pdmp.")).toBeVisible()

    // Trying it saved nothing.
    const before = await api.get<{ note_types: Array<{ key: string; label: string }> }>("/api/note-types")
    expect(before.note_types.filter((t) => t.key.startsWith("custom.")).map((t) => t.label)).not.toContain(
      TEMPLATE_LABEL,
    )

    await page.getByLabel("Note type name").fill(TYPE_LABEL)

    const saved = page.waitForResponse(
      (r) => new URL(r.url()).pathname.startsWith("/api/note-types/custom/") && r.request().method() === "PUT",
    )
    await page.getByRole("button", { name: "Save note type" }).click()
    const savedResponse = await saved
    expect(savedResponse.status(), await savedResponse.text()).toBe(200)
    const savedType = (await savedResponse.json()) as { key: string; version: number }
    slug = savedType.key.replace(/^custom\./, "")
    await expect(page.getByRole("status")).toHaveText(`Saved ${TYPE_LABEL}, version ${savedType.version}.`)
    await expect(page.getByRole("button", { name: `Edit ${TYPE_LABEL}` })).toBeVisible()

    // Book an appointment with it, inside declared working hours.
    const patient = await givePatient(api)
    await markCalendarSetupComplete(api)
    const tomorrow = practiceTomorrow()
    hoursId = (await giveWorkingHours(api, tomorrow.mondayWeekday, { start: "09:00", end: "17:00" })).id

    await page.goto("/dashboard/calendar")
    await page.getByRole("button", { name: /new appointment/i }).click()
    const patientPicker = page.getByRole("combobox", { name: "Client" })
    await patientPicker.click()
    await patientPicker.fill(patient.first_name)
    await page.getByRole("option", { name: `${patient.last_name}, ${patient.first_name}` }).click()
    await page.getByLabel("Date", { exact: true }).fill(tomorrow.iso)
    await page.getByLabel("Time", { exact: true }).fill(BOOKS_AT)

    await page.getByRole("button", { name: /More options/ }).click()
    await page.getByRole("combobox", { name: "Note type" }).click()
    await page.getByRole("option", { name: TYPE_LABEL }).click()
    await page.getByRole("combobox", { name: "Place of service" }).click()
    await page.getByRole("option", { name: "Telehealth" }).click()
    await page.getByLabel("Your location (telehealth)").fill("Office")

    const created = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/appointments" && r.request().method() === "POST",
    )
    await page.getByRole("button", { name: "Schedule", exact: true }).click()
    const createdResponse = await created
    expect(createdResponse.status(), await createdResponse.text()).toBe(201)
    const appointment = (await createdResponse.json()) as Appointment
    appointmentId = appointment.id
    expect(appointment.note_type).toBe(savedType.key)
    expect(appointment.note_inputs).toEqual({
      place_of_service: "Telehealth",
      provider_location: "Office",
    })

    // The session it starts carries the type onto its note.
    const started = await api.post<{ id: string }>(`/api/appointments/${appointment.id}/start-session`)
    const session = await api.get<Session>(`/api/sessions/${started.id}`)
    expect(session.note?.note_type).toBe(savedType.key)
  } finally {
    if (appointmentId) await api.delete(`/api/appointments/${appointmentId}`)
    if (hoursId) await api.delete(`/api/availability/rules/${hoursId}`)
    if (slug) await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
})
