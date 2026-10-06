// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The problem list and allergies, end to end: kept on the chart, shown in
 * its header, handed to note drafting, and the source a visit's diagnosis
 * codes start from.
 *
 * The stack drafts through its stand-in (NOTE_GENERATION_BASE_URL), which
 * fills every field with "Stand-in draft for <section>.<field>." and echoes
 * the chart it was handed into diagnosis and allergy fields (and SOAP's
 * clinical impression) — so a draft that shows the chart proves the chart
 * reached the model.
 */

import { randomBytes } from "node:crypto"

import type { Page } from "@playwright/test"

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Session = { id: string; status: string }
type Appointment = { id: string; diagnosis_codes: string[] | null }

async function openProblems(page: Page, patientId: string): Promise<void> {
  await page.goto(`/dashboard/patients/${patientId}?tab=problems`)
  await expect(page.getByRole("tab", { name: /Problems/ })).toHaveAttribute("data-state", "active")
}

async function addProblem(page: Page, label: string, code: string, status?: string) {
  const form = page.getByRole("form", { name: "Add a diagnosis" })
  await form.getByLabel("Diagnosis").fill(label)
  await form.getByLabel("ICD-10 code").fill(code)
  if (status) await form.getByLabel("Status").selectOption({ label: status })
  await form.getByRole("button", { name: "Add" }).click()
  await expect(page.getByTestId("problem-row").filter({ hasText: label })).toBeVisible()
}

test.describe("the problem list and allergies", () => {
  test("a clinician keeps diagnoses and allergies on the chart, and a draft is written against them", async ({
    api,
    signedInPage: page,
  }) => {
    const patient = await givePatient(api)
    await openProblems(page, patient.id)

    // An empty list is a valid list, and the header says so.
    await expect(page.getByText("No diagnoses recorded.")).toBeVisible()
    await expect(page.getByTestId("chart-diagnoses")).toHaveText("Diagnoses:None recorded")
    await expect(page.getByTestId("chart-allergies")).toContainText("Allergies:Not recorded")

    await addProblem(page, "Generalized anxiety disorder", "f41.1")
    await addProblem(page, "Insomnia", "")
    await addProblem(page, "Bipolar II disorder", "", "Rule-out")

    const rows = page.getByTestId("problem-row")
    await expect(rows.nth(0)).toContainText("Generalized anxiety disorderF41.1Active")
    await expect(rows.nth(1)).toContainText("InsomniaNo codeActive")
    await expect(rows.nth(2)).toContainText("Bipolar II disorderNo codeRule-out")
    await expect(page.getByTestId("chart-diagnoses")).toHaveText(
      "Diagnoses:Generalized anxiety disorder (F41.1); Insomnia",
    )

    // A code that is not shaped like one is caught at entry.
    const form = page.getByRole("form", { name: "Add a diagnosis" })
    await form.getByLabel("Diagnosis").fill("Anxiety")
    await form.getByLabel("ICD-10 code").fill("anxiety")
    await form.getByRole("button", { name: "Add" }).click()
    await expect(page.getByText("Enter a code like F41.1, or leave it blank.")).toBeVisible()
    await form.getByLabel("Diagnosis").fill("")
    await form.getByLabel("ICD-10 code").fill("")

    // Primary first: move Insomnia up, then resolve it.
    await page.getByRole("button", { name: "Move Insomnia up" }).click()
    await expect(page.getByTestId("chart-diagnoses")).toHaveText(
      "Diagnoses:Insomnia; Generalized anxiety disorder (F41.1)",
    )
    await rows.filter({ hasText: "Insomnia" }).getByRole("button", { name: "Resolve" }).click()
    await expect(page.getByTestId("chart-diagnoses")).toHaveText(
      "Diagnoses:Generalized anxiety disorder (F41.1)",
    )
    await expect(rows.nth(2)).toContainText("InsomniaNo codeResolved")

    // No known drug allergies is its own answer, not an empty list.
    await page.getByRole("button", { name: "Edit allergies" }).click()
    await page.getByRole("radio", { name: "No known drug allergies" }).check()
    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByTestId("chart-allergies")).toContainText(
      "Allergies:No known drug allergies",
    )
    const stored = await api.get<{ allergy_status: string; allergies: unknown[] }>(
      `/api/patients/${patient.id}`,
    )
    expect(stored).toMatchObject({ allergy_status: "nkda", allergies: [] })

    // A SOAP draft names the active problem with its code; the rule-out and
    // the resolved problem are not diagnoses on it.
    const session = await api.post<Session>(`/api/patients/${patient.id}/sessions/upload`, {
      patient_id: patient.id,
      session_date: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
      transcript: { format: "txt", content: "[00:00:05] Therapist: How has the worry been?" },
    })
    await expect
      .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status)
      .toBe("pending_review")
    await page.goto(`/dashboard/sessions/${session.id}`)
    await expect(
      page.getByText(
        "Problem list: F41.1 Generalized anxiety disorder; Bipolar II disorder — rule-out, not a diagnosis.",
      ),
    ).toBeVisible()
  })

  test("a prescriber's draft carries the chart's allergies, and the visit's codes start from the active problems", async ({
    api,
    signedInPage: page,
  }) => {
    const patient = await givePatient(api)
    const problems = `/api/patients/${patient.id}/problems`
    await api.post(problems, { label: "Generalized anxiety disorder", icd10_code: "F41.1" })
    await api.post(problems, { label: "Major depressive disorder, recurrent", icd10_code: "F33.1" })
    await api.post(problems, { label: "PTSD", icd10_code: "F43.10", status: "resolved" })
    await api.post(problems, { label: "Insomnia" })
    await api.put(`/api/patients/${patient.id}/allergies`, {
      status: "recorded",
      allergies: [{ substance: "Penicillin", reaction: "Hives" }],
    })

    const slug = `e2e_rx_${randomBytes(3).toString("hex")}`
    let appointmentId: string | null = null
    await api.put(`/api/note-types/custom/${slug}`, {
      label: `Prescriber follow-up ${slug}`,
      sections: [
        {
          key: "assessment",
          label: "Assessment",
          fields: [
            { key: "diagnoses", label: "Diagnoses" },
            { key: "allergies", label: "Allergies" },
          ],
        },
      ],
    })
    try {
      const startAt = new Date(Date.now() - (30 + Math.floor(Math.random() * 500)) * 60 * 60 * 1000)
      const appointment = await api.post<Appointment>("/api/appointments", {
        patient_id: patient.id,
        title: "Follow-up",
        start_at: startAt.toISOString(),
        end_at: new Date(startAt.getTime() + 30 * 60 * 1000).toISOString(),
        duration_minutes: 30,
        session_type: "individual",
        note_type: `custom.${slug}`,
      })
      appointmentId = appointment.id
      expect(appointment.diagnosis_codes).toBeNull()

      // Starting the visit pre-fills its codes from the active problems, in
      // order; the resolved and the uncoded problems contribute nothing.
      const session = await api.post<Session>(`/api/appointments/${appointment.id}/start-session`, {
        recording: true,
      })
      const started = await api.get<Appointment>(`/api/appointments/${appointment.id}`)
      expect(started.diagnosis_codes).toEqual(["F41.1", "F33.1"])

      // The codes billed on a date are the visit's own, and stay editable.
      await api.patch(`/api/appointments/${appointment.id}`, { diagnosis_codes: ["F33.1"] })
      expect((await api.get<Appointment>(`/api/appointments/${appointment.id}`)).diagnosis_codes).toEqual(
        ["F33.1"],
      )

      await api.patch(`/api/sessions/${session.id}/status`, { status: "in_progress" })
      await api.patch(`/api/sessions/${session.id}/status`, { status: "recording_complete" })
      await api.post(`/api/sessions/${session.id}/transcript`, {
        format: "txt",
        content: "[00:00:05] Therapist: Any reactions to the new medication?",
      })
      await expect
        .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status)
        .toBe("pending_review")

      await page.goto(`/dashboard/sessions/${session.id}`)
      await expect(page.getByText(/Allergies: Penicillin \(Hives\)\./)).toBeVisible()
      await expect(
        page.getByText(
          /Problem list: F41\.1 Generalized anxiety disorder; F33\.1 Major depressive disorder, recurrent; Insomnia \(no code recorded\)\./,
        ),
      ).toBeVisible()
    } finally {
      if (appointmentId) await api.delete(`/api/appointments/${appointmentId}`)
      await api.request("DELETE", `/api/note-types/custom/${slug}`)
    }
  })
})
