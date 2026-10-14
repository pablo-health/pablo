// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A blank psychiatric follow-up opens with what Pablo already knows: the
 * chart's medications and allergies and the place of service chosen in the
 * dialog, in the editor, where the clinician can change them.
 *
 * Catches the blank-note path storing the note empty, so a clinician
 * retyped the medication list, the allergies and the telehealth attestation
 * a drafted note prints from the chart.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Note = {
  id: string
  content: Record<string, unknown> | null
  content_edited: Record<string, Record<string, unknown>> | null
}

const FOLLOW_UP_LABEL = "Psychiatric follow-up (E/M + psychotherapy)"

test("a blank psychiatric follow-up opens with the chart and the place of service filled in", async ({
  api,
  signedInPage: page,
}) => {
  const patient = await givePatient(api)
  await api.post(`/api/patients/${patient.id}/medications`, {
    drug_name: "Sertraline",
    dose: "100 mg",
    frequency: "every morning",
    category: "psychiatric",
  })
  await api.put(`/api/patients/${patient.id}/allergies`, {
    status: "recorded",
    allergies: [{ substance: "Penicillin", reaction: "Hives" }],
  })

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByLabel("Notes").getByRole("button", { name: "New note" }).click()
  const dialog = page.getByRole("dialog")
  await dialog.getByRole("button", { name: new RegExp(`^${escapeRegExp(FOLLOW_UP_LABEL)}`) }).click()
  await dialog.getByRole("combobox", { name: "Place of service" }).click()
  await page.getByRole("option", { name: "Telehealth" }).click()

  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/patients/${patient.id}/notes`) &&
      response.request().method() === "POST",
  )
  await dialog.getByRole("button", { name: "Create" }).click()
  const response = await created
  expect(response.status(), await response.text()).toBe(201)
  const note = (await response.json()) as Note
  // Filled in by code from the chart, not drafted.
  expect(note.content).toBeNull()
  await page.waitForURL(`**/dashboard/patients/${patient.id}/notes/${note.id}`)

  const medications = page.getByRole("textbox", { name: "Current medications", exact: true })
  const allergies = page.getByRole("textbox", { name: "Allergies", exact: true })
  const placeOfService = page.getByRole("textbox", { name: "Place of service", exact: true })
  await expect(medications).toHaveValue("Psychiatric: Sertraline 100 mg, every morning")
  await expect(allergies).toHaveValue("Penicillin (Hives)")
  await expect(placeOfService).toHaveValue(
    /^Visit conducted by synchronous audio and video telehealth/,
  )
  // Nothing the visit has to say is filled in for it.
  await expect(page.getByRole("textbox", { name: "Chief complaint", exact: true })).toHaveValue("")
  await expect(page.getByText("AI Generated")).toHaveCount(0)

  await expect(allergies).toBeEditable()
  await allergies.fill("Penicillin (Hives); latex")
  await page.getByRole("button", { name: "Save Changes" }).click()

  await expect
    .poll(async () => {
      const saved = await api.get<Note>(`/api/notes/${note.id}`)
      return saved.content_edited?.medications?.allergies
    })
    .toBe("Penicillin (Hives); latex")
})

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}
