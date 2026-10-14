// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A blank psychiatric follow-up opens with what Pablo already knows: every
 * box the chart backs, filled in from the chart, and the place of service
 * chosen in the dialog, in the editor where the clinician can change them.
 * A box the chart has nothing for is empty: no "Not recorded" or the like.
 *
 * Catches the blank-note path storing the note empty, so a clinician
 * retyped the medication list, the allergies, the history and the
 * telehealth attestation a drafted note prints from the chart; and a fill
 * that writes a placeholder where the chart is silent.
 */

import type { Page } from "@playwright/test"
import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"
import type { ApiClient } from "../fixtures/api"

type Note = {
  id: string
  content: Record<string, unknown> | null
  content_edited: Record<string, Record<string, unknown>> | null
}

const FOLLOW_UP_LABEL = "Psychiatric follow-up (E/M + psychotherapy)"

/** Each chart-history field the follow-up prints, by key, with its box's label. */
const HISTORY_BOXES: Record<string, string> = {
  alcohol: "Alcohol",
  cannabis: "Cannabis",
  stimulants: "Stimulants",
  cocaine: "Cocaine",
  opioids: "Opioids",
  benzodiazepines: "Benzodiazepines",
  tobacco_nicotine: "Tobacco / nicotine",
  other_substances: "Other substances",
  prior_diagnoses: "Earlier diagnoses",
  psychotherapy_history: "Psychotherapy",
  medication_trials: "Psychiatric medications tried",
  hospitalizations: "Hospitalizations and programs",
  past_self_harm: "Past suicide attempts, self-harm or harm to others",
  legal_custody: "Legal or custody involvement",
  trauma_history: "Trauma history",
  living_situation: "Living situation",
  relationships: "Relationships",
  work_school: "Work or school",
  supports: "Supports",
  cultural_considerations: "Cultural considerations",
  medical_history: "Medical history",
  family_psychiatric: "Psychiatric",
  family_medical: "Medical",
  family_treatment_response: "Medications that helped relatives",
}

/** What a box must never hold when Pablo has nothing for it. */
const PLACEHOLDER = /not recorded|none recorded|not stated|not asked|\bn\/a\b|\(none\)|^none$|^—$/i

const TELEHEALTH = /^Visit conducted by synchronous audio and video telehealth/

async function chartMedicationsAndAllergies(api: ApiClient, patientId: string): Promise<void> {
  await api.post(`/api/patients/${patientId}/medications`, {
    drug_name: "Sertraline",
    dose: "100 mg",
    frequency: "every morning",
    category: "psychiatric",
  })
  await api.put(`/api/patients/${patientId}/allergies`, {
    status: "recorded",
    allergies: [{ substance: "Penicillin", reaction: "Hives" }],
  })
}

async function startBlankTelehealthFollowUp(page: Page, patientId: string): Promise<Note> {
  await page.goto(`/dashboard/patients/${patientId}`)
  await page.getByLabel("Notes").getByRole("button", { name: "New note" }).click()
  const dialog = page.getByRole("dialog")
  await dialog.getByRole("button", { name: new RegExp(`^${escapeRegExp(FOLLOW_UP_LABEL)}`) }).click()
  await dialog.getByRole("combobox", { name: "Place of service" }).click()
  await page.getByRole("option", { name: "Telehealth" }).click()

  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/patients/${patientId}/notes`) &&
      response.request().method() === "POST",
  )
  await dialog.getByRole("button", { name: "Create" }).click()
  const response = await created
  expect(response.status(), await response.text()).toBe(201)
  const note = (await response.json()) as Note
  // Filled in by code from the chart, not drafted.
  expect(note.content).toBeNull()
  await page.waitForURL(`**/dashboard/patients/${patientId}/notes/${note.id}`)
  return note
}

function box(page: Page, label: string) {
  return page.getByRole("textbox", { name: label, exact: true })
}

test("a blank follow-up for a client with a full chart opens with every chart box filled in", async ({
  api,
  signedInPage: page,
}) => {
  const patient = await givePatient(api)
  await chartMedicationsAndAllergies(api, patient.id)
  await api.post(`/api/patients/${patient.id}/problems`, {
    label: "Generalized anxiety disorder",
    icd10_code: "F41.1",
  })
  for (const key of Object.keys(HISTORY_BOXES)) {
    await api.put(`/api/patients/${patient.id}/chart-history/${key}`, {
      text: `Charted ${key}.`,
    })
  }

  const note = await startBlankTelehealthFollowUp(page, patient.id)

  await expect(box(page, "Place of service")).toHaveValue(TELEHEALTH)
  await expect(box(page, "Current medications")).toHaveValue(
    "Psychiatric: Sertraline 100 mg, every morning",
  )
  await expect(box(page, "Allergies")).toHaveValue("Penicillin (Hives)")
  for (const [key, label] of Object.entries(HISTORY_BOXES)) {
    // The chart's text alone: nothing was heard, so no mark about this visit.
    await expect(box(page, label), label).toHaveValue(`Charted ${key}.`)
  }
  const diagnoses = page.getByRole("group", { name: "Diagnoses" })
  await expect(diagnoses.getByRole("textbox", { name: "Diagnosis 1" })).toHaveValue(
    "Generalized anxiety disorder",
  )
  await expect(diagnoses.getByRole("textbox", { name: "Code 1" })).toHaveValue("F41.1")
  // Nothing the visit has to say is filled in for it.
  await expect(box(page, "Chief complaint")).toHaveValue("")
  await expect(page.getByText("AI Generated")).toHaveCount(0)

  const allergies = box(page, "Allergies")
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

test("a blank follow-up for a client with a partial chart fills only what the chart has", async ({
  api,
  signedInPage: page,
}) => {
  const patient = await givePatient(api)
  await chartMedicationsAndAllergies(api, patient.id)

  await startBlankTelehealthFollowUp(page, patient.id)

  await expect(box(page, "Place of service")).toHaveValue(TELEHEALTH)
  await expect(box(page, "Current medications")).toHaveValue(
    "Psychiatric: Sertraline 100 mg, every morning",
  )
  await expect(box(page, "Allergies")).toHaveValue("Penicillin (Hives)")
  for (const label of Object.values(HISTORY_BOXES)) {
    await expect(box(page, label), label).toHaveValue("")
  }
  const diagnoses = page.getByRole("group", { name: "Diagnoses" })
  await expect(diagnoses.getByRole("textbox", { name: "Diagnosis 1" })).toHaveCount(0)

  // No box anywhere holds a stand-in for something Pablo does not know.
  for (const value of await page.getByRole("textbox").evaluateAll((boxes) =>
    boxes.map((b) => (b as HTMLTextAreaElement | HTMLInputElement).value),
  )) {
    expect(value).not.toMatch(PLACEHOLDER)
  }
})

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}
