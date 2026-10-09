// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Chart history, end to end: a history field is edited on the client page,
 * survives a reload with when it was updated, keeps the value it replaced,
 * and is printed in a prescriber's draft as written.
 *
 * A field that names a chart entry as its source is written from the chart
 * in code; the stack's stand-in model (NOTE_GENERATION_BASE_URL) is asked
 * only what the visit said about it, and answers a client line
 * "Update on <key>: <text>" with that text, citing the line.
 */

import { randomBytes } from "node:crypto"

import type { Page } from "@playwright/test"

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Session = { id: string; status: string }
type Appointment = { id: string }

async function openHistory(page: Page, patientId: string): Promise<void> {
  await page.goto(`/dashboard/patients/${patientId}?tab=history`)
  await expect(page.getByRole("tab", { name: /History/ })).toHaveAttribute("data-state", "active")
}

/** Do ``save`` and wait until the server has the value, so a reload reads it back. */
async function saved(page: Page, save: () => Promise<void>): Promise<void> {
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/chart-history/") && r.request().method() === "PUT" && r.ok(),
    ),
    save(),
  ])
}

test.describe("chart history", () => {
  test("a history field is edited on the chart, kept across a reload, and its earlier value stays", async ({
    api,
    signedInPage: page,
  }) => {
    const patient = await givePatient(api)
    await openHistory(page, patient.id)

    const field = page.getByTestId("history-field-living_situation")
    await expect(field).toContainText("Not recorded")

    await field.getByRole("button", { name: "Add Living situation" }).click()
    await field.getByLabel("Living situation").fill("Lives with spouse and two children.")
    await saved(page, () => field.getByRole("button", { name: "Save" }).click())
    await expect(field).toContainText("Lives with spouse and two children.")

    await page.reload()
    await expect(field).toContainText("Lives with spouse and two children.")
    await expect(field).toContainText(/Last updated /)

    await field.getByRole("button", { name: "Edit Living situation" }).click()
    await field.getByLabel("Living situation").fill("Separated in August; lives alone.")
    await saved(page, () => field.getByRole("button", { name: "Save" }).click())

    await page.reload()
    await expect(field.getByText("Separated in August; lives alone.")).toBeVisible()
    await field.getByText("Earlier values (1)").click()
    await expect(field.getByText("Lives with spouse and two children.")).toBeVisible()

    // A value entered in error is removed; the field reads empty and keeps it as history.
    page.once("dialog", (dialog) => dialog.accept())
    await field.getByRole("button", { name: "Remove Living situation" }).click()
    await expect(field.getByText("Not recorded", { exact: true })).toBeVisible()
    await page.reload()
    await expect(field.getByText("Earlier values (2)")).toBeVisible()
  })

  test("a prescriber's draft writes the chart's history as recorded, and marks what the visit changed", async ({
    api,
    signedInPage: page,
  }) => {
    const patient = await givePatient(api)
    await api.put(`/api/patients/${patient.id}/chart-history/trauma_history`, {
      text: "Car accident at 19; no ongoing symptoms.",
    })
    await api.put(`/api/patients/${patient.id}/chart-history/work_school`, {
      text: "Employed at a logistics firm.",
    })
    await api.put(`/api/patients/${patient.id}/allergies`, {
      status: "recorded",
      allergies: [{ substance: "Sulfa", reaction: "Rash" }],
    })

    // Each field names the chart entry it prints, so code writes it from the
    // chart and the model is asked only what the visit said about it.
    const slug = `e2e_history_${randomBytes(3).toString("hex")}`
    let appointmentId: string | null = null
    await api.put(`/api/note-types/custom/${slug}`, {
      label: `Prescriber follow-up ${slug}`,
      sections: [
        {
          key: "trauma_history",
          label: "Trauma history",
          fields: [{ key: "trauma_history", label: "Trauma history", source: "trauma_history" }],
        },
        {
          key: "social_history",
          label: "Social history",
          fields: [{ key: "work_school", label: "Work or school", source: "work_school" }],
        },
        {
          key: "medications",
          label: "Medications and allergies",
          fields: [{ key: "allergies", label: "Allergies", source: "allergies" }],
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
          "[00:00:05] Therapist: How has sleep been since the last visit?\n" +
          "[00:00:09] Client: Update on work_school: laid off last week.",
      })
      await expect
        .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status)
        .toBe("pending_review")

      await page.goto(`/dashboard/sessions/${session.id}`)
      // Unchanged: the chart's text alone. Changed: the chart's text, then the visit's, marked.
      await expect(
        page.getByText("Car accident at 19; no ongoing symptoms.", { exact: true }),
      ).toBeVisible()
      await expect(
        page.getByText('Employed at a logistics firm. (stated this visit: "laid off last week.")'),
      ).toBeVisible()
      // The stand-in says nothing about allergies: the chart's record prints as entered.
      await expect(page.getByText("Sulfa (Rash)", { exact: true })).toBeVisible()
    } finally {
      if (appointmentId) await api.delete(`/api/appointments/${appointmentId}`)
      await api.request("DELETE", `/api/note-types/custom/${slug}`)
    }
  })
})
