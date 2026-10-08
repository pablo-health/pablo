// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The medication list, end to end: a medication is recorded with its
 * frequency and whether it is psychiatric, shown that way on the chart, and
 * handed to a prescriber's draft as the current list.
 *
 * The stack drafts through its stand-in (NOTE_GENERATION_BASE_URL), which
 * fills a current-medications field with the chart's list line for line, then
 * what a client line says they take that the chart lacks — so a draft that
 * shows the list proves the chart reached the model.
 */

import { randomBytes } from "node:crypto"

import type { Page } from "@playwright/test"

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Session = { id: string; status: string }
type Appointment = { id: string }
type Medication = { drug_name: string; dose: string; frequency: string | null; category: string | null }

async function openMedications(page: Page, patientId: string): Promise<void> {
  await page.goto(`/dashboard/patients/${patientId}?tab=medications`)
  await expect(page.getByRole("tab", { name: /Medications/ })).toHaveAttribute(
    "data-state",
    "active",
  )
}

test.describe("the medication list", () => {
  test("a medication is recorded with its frequency and category, and the chart shows both", async ({
    api,
    signedInPage: page,
  }) => {
    const patient = await givePatient(api)
    await openMedications(page, patient.id)
    await expect(page.getByText("No medications recorded.")).toBeVisible()

    await page.getByRole("button", { name: "Add medication" }).click()
    const dialog = page.getByRole("dialog", { name: "Add medication" })
    await dialog.getByLabel("Drug name *").fill("Sertraline")
    await dialog.getByLabel("Dose *").fill("100 mg")
    await dialog.getByLabel("Frequency").fill("every morning")
    await dialog.getByLabel("Category").click()
    await page.getByRole("option", { name: "Psychiatric" }).click()
    await dialog.getByRole("button", { name: "Add medication" }).click()
    await expect(dialog).toBeHidden()

    await page.reload()
    await expect(page.getByText("100 mg, every morning")).toBeVisible()
    await expect(page.getByText("Psychiatric", { exact: true })).toBeVisible()

    const stored = await api.get<{ data: Medication[] }>(`/api/patients/${patient.id}/medications`)
    expect(stored.data).toEqual([
      expect.objectContaining({
        drug_name: "Sertraline",
        dose: "100 mg",
        frequency: "every morning",
        category: "psychiatric",
      }),
    ])

    // Changing the schedule leaves the dose as it was.
    await page.getByRole("button", { name: "Edit Sertraline" }).click()
    const edit = page.getByRole("dialog", { name: "Edit medication" })
    await edit.getByLabel("Frequency").fill("at bedtime")
    await edit.getByRole("button", { name: "Save changes" }).click()
    await expect(edit).toBeHidden()
    await expect(page.getByText("100 mg, at bedtime")).toBeVisible()
  })

  test("a prescriber's draft lists the chart's current medications, psychiatric and other apart", async ({
    api,
    signedInPage: page,
  }) => {
    const patient = await givePatient(api)
    const medications = `/api/patients/${patient.id}/medications`
    await api.post(medications, {
      drug_name: "Sertraline",
      dose: "100 mg",
      frequency: "every morning",
      category: "psychiatric",
    })
    await api.post(medications, {
      drug_name: "Lisinopril",
      dose: "10 mg",
      frequency: "daily",
      category: "other",
    })
    await api.post(medications, { drug_name: "Hydroxyzine", dose: "25 mg", status: "discontinued" })

    const slug = `e2e_meds_${randomBytes(3).toString("hex")}`
    let appointmentId: string | null = null
    await api.put(`/api/note-types/custom/${slug}`, {
      label: `Prescriber follow-up ${slug}`,
      sections: [
        {
          key: "medications",
          label: "Medications",
          fields: [{ key: "current_medications", label: "Current medications", kind: "list" }],
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
      const session = await api.post<Session>(`/api/appointments/${appointment.id}/start-session`, {
        recording: true,
      })
      await api.patch(`/api/sessions/${session.id}/status`, { status: "in_progress" })
      await api.patch(`/api/sessions/${session.id}/status`, { status: "recording_complete" })
      await api.post(`/api/sessions/${session.id}/transcript`, {
        format: "txt",
        content:
          "[00:00:02] Client: I'm taking melatonin 3 mg.\n" +
          "[00:00:05] Therapist: Let's start bupropion XL 150 mg every morning.",
      })
      await expect
        .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status)
        .toBe("pending_review")

      await page.goto(`/dashboard/sessions/${session.id}`)
      await expect(page.getByText("Sertraline 100 mg, every morning")).toBeVisible()
      await expect(page.getByText("Lisinopril 10 mg, daily")).toBeVisible()
      await expect(page.getByText("Psychiatric:")).toBeVisible()
      await expect(page.getByText("Other:")).toBeVisible()
      await expect(page.getByText(/Hydroxyzine/)).toHaveCount(0)
      // What the client says they take that the chart lacks follows the list, marked.
      await expect(page.getByText('"melatonin 3 mg" (stated this visit)')).toBeVisible()
    } finally {
      if (appointmentId) await api.delete(`/api/appointments/${appointmentId}`)
      await api.request("DELETE", `/api/note-types/custom/${slug}`)
    }
  })
})
