// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Importing a note written in another records system as a practice's own
 * note type: the psychiatric follow-up template, saved as the practice's
 * type, then a text note imported into it from the patient's "New note".
 *
 * The stack reads imports through its stand-in (scripts/fake_llm.py), which
 * relocates each "Label: text" line of the document into the field of that
 * name — so the note opens in the editor with the document's own words in
 * the follow-up's fields.
 *
 * An imported note records a visit documented and signed where it happened,
 * so it is kept as a reference copy: never among the notes to sign, never
 * offered for signing, with the place of service and signer its document
 * states, with its chart updates accepted on its own page, and with the
 * uploaded file kept in the client's documents and opened from the note.
 */

import { randomBytes } from "node:crypto"
import { readFile } from "node:fs/promises"

import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { givePatient } from "../fixtures/scenarios"

type Template = { spec: { label: string } }
type Imported = { id: string; note: { note_type: string } | null }

const TEMPLATE = new URL(
  "../../../backend/app/notes/templates/psychiatric_follow_up.json",
  import.meta.url,
)

test("a clinician imports a note as the practice's follow-up type and sees its fields", async ({
  signedInPage: page,
  api,
}) => {
  const template = JSON.parse(await readFile(TEMPLATE, "utf8")) as Template
  const slug = `e2e_import_${randomBytes(3).toString("hex")}`
  const label = `${template.spec.label} ${slug}`
  await api.request("PUT", `/api/note-types/custom/${slug}`, { ...template.spec, label })

  try {
    const patient = await givePatient(api)
    const marker = `e2e-${Date.now().toString(36)}`
    const document = [
      "Psychiatric medication management follow-up",
      `Chief complaint: Still waking at 4 a.m. ${marker}`,
      "Depression: Mood steadier since the dose increase; back at work full time.",
      "Current medications: Sertraline 100 mg by mouth daily",
      "Allergies: Penicillin (rash).",
      "Follow up: Return in 6 weeks.",
    ].join("\n")

    await page.goto(`/dashboard/patients/${patient.id}`)
    await page.getByLabel("Notes").getByRole("button", { name: "New note" }).click()
    await page.getByRole("dialog").getByRole("button", { name: /Import existing notes/ }).click()

    const dialog = page.getByRole("dialog", { name: "Import existing notes" })
    await dialog.getByRole("combobox", { name: "Import as" }).click()
    await page.getByRole("option", { name: label }).click()
    await expect(dialog.getByRole("combobox", { name: "Import as" })).toHaveText(label)
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
    expect(session.note?.note_type).toBe(`custom.${slug}`)
    await expect(dialog.getByRole("status")).toHaveText("1 added")

    // The note opens in the follow-up's own fields, holding the document's words.
    // (The document itself is shown beside it, so look inside the note.)
    await page.goto(`/dashboard/sessions/${session.id}`)
    const note = page.getByTestId("session-note")
    await expect(note.getByText(`Still waking at 4 a.m. ${marker}`)).toBeVisible()
    await expect(
      note.getByText("Mood steadier since the dose increase; back at work full time."),
    ).toBeVisible()
    await expect(note.getByText("Sertraline 100 mg by mouth daily")).toBeVisible()
    await expect(note.getByText("Penicillin (rash).")).toBeVisible()
    await expect(note.getByText("Return in 6 weeks.")).toBeVisible()
    // Each field also says whether the document has it.
    await expect(note.getByRole("heading", { name: /^Depression/ })).toContainText("from your note")
    await expect(note.getByText("Mental status exam", { exact: true })).toBeVisible()
    // Reviewed beside the note, not part of it.
    await expect(note.getByText("Medical decision making", { exact: true })).toHaveCount(0)
  } finally {
    await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
})

type Summary = {
  notes_pending_count: number
  awaiting_review_total: number
  awaiting_review: { session_id: string }[]
}

const SIGNER = "A. Rivera, PMHNP-BC"

function summary(api: ApiClient): Promise<Summary> {
  const day = new Date()
  const start = new Date(day.getFullYear(), day.getMonth(), day.getDate())
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000)
  const weekEnd = new Date(start.getTime() + 7 * 24 * 60 * 60 * 1000)
  const query = new URLSearchParams({
    today_start: start.toISOString(),
    today_end: end.toISOString(),
    week_start: end.toISOString(),
    week_end: weekEnd.toISOString(),
  })
  return api.get<Summary>(`/api/dashboard/summary?${query}`)
}

test("an imported note is a reference copy: never to sign, with its details and chart updates", async ({
  signedInPage: page,
  api,
}) => {
  const template = JSON.parse(await readFile(TEMPLATE, "utf8")) as Template
  const slug = `e2e_ref_${randomBytes(3).toString("hex")}`
  const label = `${template.spec.label} ${slug}`
  await api.request("PUT", `/api/note-types/custom/${slug}`, { ...template.spec, label })

  try {
    const patient = await givePatient(api)
    const before = await summary(api)
    // The stand-in reads "Label: text" lines; the proposal call answers the
    // numbered "Client: Update on <key>: <text>" paragraph.
    const document = [
      "Psychiatric medication management follow-up",
      "Place of service: Telehealth",
      "Chief complaint: Sleep is better on the new dose.",
      "Client: Update on supports: Sister and a neighbor.",
      "Follow up: Return in 6 weeks.",
      `Signed by: ${SIGNER}`,
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
    const session = (await response.json()) as Imported & { status: string }
    expect(session.status).toBe("imported")

    // Never among the notes to sign: neither count moves, and it is not listed.
    const after = await summary(api)
    expect(after.notes_pending_count).toBe(before.notes_pending_count)
    expect(after.awaiting_review_total).toBe(before.awaiting_review_total)
    expect(after.awaiting_review.map((row) => row.session_id)).not.toContain(session.id)

    await dialog.getByRole("button", { name: "Open note" }).click()
    await expect(page).toHaveURL(new RegExp(`/dashboard/sessions/${session.id}$`))

    // Labelled as imported, with who signed it there as the document writes it.
    const notice = page.getByRole("region", { name: "Imported note" })
    await expect(notice).toContainText("Imported")
    await expect(notice).toContainText("Documented elsewhere for the visit on")
    await expect(notice).toContainText(`Signed by ${SIGNER}`)

    // The uploaded file itself is kept, and opens from the note.
    const opened = page.waitForEvent("popup")
    await page.getByRole("button", { name: "Open original file" }).click()
    const original = await opened
    await expect(original.locator("body")).toContainText(`Signed by: ${SIGNER}`)
    await original.close()

    // No way to sign it.
    await expect(page.getByTestId("session-note")).toBeVisible()
    await expect(page.getByRole("button", { name: /Sign and lock/ })).toHaveCount(0)
    await expect(page.getByRole("heading", { name: "Review Session" })).toHaveCount(0)

    // The place of service the document states, read-only.
    const details = page.getByRole("region", { name: "Note details" })
    await expect(details.getByText("Telehealth", { exact: true })).toBeVisible()
    await expect(details.getByRole("combobox")).toHaveCount(0)

    // Its chart updates are accepted here, and reach the chart.
    const updates = page.getByRole("region", { name: "Chart updates" })
    const supports = updates.getByRole("listitem", {
      name: "Social history and supports: Supports",
    })
    await supports.getByRole("button", { name: "Accept" }).click()
    await expect(supports.getByRole("button", { name: "Accept" })).toHaveCount(0)
    await expect
      .poll(async () => historyText(api, patient.id, "supports"))
      .toBe("Sister and a neighbor.")
    expect((await api.get<{ status: string }>(`/api/sessions/${session.id}`)).status).toBe(
      "imported",
    )

    // And it is listed among the client's documents.
    await page.goto(`/dashboard/patients/${patient.id}?tab=documents`)
    await expect(page.getByText("transfer-note.txt")).toBeVisible()
  } finally {
    await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
})

type HistoryField = { key: string; text: string | null }
type ChartHistory = { groups: { fields: HistoryField[] }[] }

async function historyText(api: ApiClient, patientId: string, key: string) {
  const chart = await api.get<ChartHistory>(`/api/patients/${patientId}/chart-history`)
  return chart.groups.flatMap((g) => g.fields).find((f) => f.key === key)?.text
}
