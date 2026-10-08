// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A client arriving from another records system: their last note is imported
 * as the practice's follow-up type, and signing it fills their empty chart.
 * The note shows which fields were found in the document; at sign it
 * proposes each history field it states, and the document's own paragraph
 * where one says what changed; accepting them all fills the chart, and the
 * next follow-up prints it.
 *
 * The stack reads imports through its stand-in (scripts/fake_llm.py): the
 * parse relocates each "Label: text" line into the field of that name, and
 * the proposal call answers a numbered "Client: Update on <key>: <text>"
 * paragraph with a proposal for that field citing it. Its drafts fill a
 * field whose key is a chart-history key with the chart's text.
 */

import { randomBytes } from "node:crypto"
import { readFile } from "node:fs/promises"

import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { givePatient } from "../fixtures/scenarios"

type Template = { spec: { label: string } }
type Session = { id: string; status: string }
type Imported = { id: string; note: { id: string; note_type: string } | null }
type Appointment = { id: string }
type HistoryField = { key: string; text: string | null; source_note_id: string | null }
type ChartHistory = { groups: { fields: HistoryField[] }[] }

const TEMPLATE = new URL(
  "../../../backend/app/notes/templates/psychiatric_follow_up.json",
  import.meta.url,
)

async function historyText(api: ApiClient, patientId: string, key: string) {
  const chart = await api.get<ChartHistory>(`/api/patients/${patientId}/chart-history`)
  return chart.groups.flatMap((g) => g.fields).find((f) => f.key === key)?.text
}

test("an imported note fills an empty chart when it is signed, and the next follow-up prints it", async ({
  signedInPage: page,
  api,
}) => {
  const template = JSON.parse(await readFile(TEMPLATE, "utf8")) as Template
  const slug = `e2e_seed_${randomBytes(3).toString("hex")}`
  const label = `${template.spec.label} ${slug}`
  await api.request("PUT", `/api/note-types/custom/${slug}`, { ...template.spec, label })
  const appointments: string[] = []

  try {
    const patient = await givePatient(api)
    const marker = `e2e-${Date.now().toString(36)}`
    const document = [
      "Psychiatric medication management follow-up",
      `Chief complaint: Still waking at 4 a.m. ${marker}`,
      "Relationships: Married, two children.",
      "Work school: Teaches fourth grade.",
      "Client: Update on supports: Sister and a neighbor.",
      "Follow up: Return in 6 weeks.",
    ].join("\n\n")

    await page.goto(`/dashboard/patients/${patient.id}`)
    await page.getByLabel("Notes").getByRole("button", { name: "New note" }).click()
    await page.getByRole("dialog").getByRole("button", { name: /Import existing notes/ }).click()
    const dialog = page.getByRole("dialog", { name: "Import existing notes" })
    await dialog.getByRole("combobox", { name: "Import as" }).click()
    await page.getByRole("option", { name: label }).click()
    await dialog.getByLabel("Choose note files to import").setInputFiles({
      name: "transfer-note.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(document),
    })
    const imported = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/patients/${patient.id}/sessions/import`) &&
        response.request().method() === "POST",
    )
    await dialog.getByRole("button", { name: "Import 1 note" }).click()
    const response = await imported
    expect(response.status(), await response.text()).toBe(201)
    const session = (await response.json()) as Imported
    await expect(dialog.getByRole("status")).toHaveText("1 added")

    // Each field says whether the document has it.
    await page.goto(`/dashboard/sessions/${session.id}`)
    const note = page.getByTestId("session-note")
    await expect(note.getByRole("heading", { name: /^Relationships/ })).toContainText(
      "from your note",
    )

    await page.getByRole("button", { name: "Sign and lock" }).click()
    const sign = page.getByRole("dialog")
    await expect(sign.getByRole("heading", { name: "Update the chart" })).toBeVisible()
    const supports = sign.getByRole("listitem", { name: "Social history and supports: Supports" })
    await expect(supports).toContainText("Client: Update on supports: Sister and a neighbor.")
    const relationships = sign.getByRole("listitem", {
      name: "Social history and supports: Relationships",
    })
    await expect(relationships).toContainText("Recorded this visit")
    const rows = sign.getByTestId("chart-proposal")
    await expect(rows).toHaveCount(3)
    for (const name of ["Relationships", "Work or school", "Supports"]) {
      await sign
        .getByRole("listitem", { name: `Social history and supports: ${name}` })
        .getByRole("button", { name: "Accept" })
        .click()
    }
    await sign.getByRole("button", { name: "Sign and lock" }).click()
    await expect
      .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status)
      .toBe("finalized")

    expect(await historyText(api, patient.id, "relationships")).toBe("Married, two children.")
    expect(await historyText(api, patient.id, "work_school")).toBe("Teaches fourth grade.")
    expect(await historyText(api, patient.id, "supports")).toBe("Sister and a neighbor.")

    // The next follow-up is drafted against the chart the import filled.
    const startAt = new Date(Date.now() - 2 * 60 * 60 * 1000)
    const appointment = await api.post<Appointment>("/api/appointments", {
      patient_id: patient.id,
      title: "Follow-up",
      start_at: startAt.toISOString(),
      end_at: new Date(startAt.getTime() + 30 * 60 * 1000).toISOString(),
      duration_minutes: 30,
      session_type: "individual",
      note_type: `custom.${slug}`,
      note_inputs: { place_of_service: "In office" },
    })
    appointments.push(appointment.id)
    const next = await api.post<Session>(`/api/appointments/${appointment.id}/start-session`, {
      recording: true,
    })
    await api.patch(`/api/sessions/${next.id}/status`, { status: "in_progress" })
    await api.patch(`/api/sessions/${next.id}/status`, { status: "recording_complete" })
    await api.post(`/api/sessions/${next.id}/transcript`, {
      format: "txt",
      content: "[00:00:05] Therapist: How have things been?",
    })
    await expect
      .poll(async () => (await api.get<Session>(`/api/sessions/${next.id}`)).status)
      .toBe("pending_review")
    await page.goto(`/dashboard/sessions/${next.id}`)
    const followUp = page.getByTestId("session-note")
    for (const text of ["Married, two children.", "Teaches fourth grade.", "Sister and a neighbor."]) {
      await expect(followUp.getByText(text, { exact: true })).toBeVisible()
    }
  } finally {
    for (const id of appointments) await api.delete(`/api/appointments/${id}`)
    await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
})
