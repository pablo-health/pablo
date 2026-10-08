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
 */

import { randomBytes } from "node:crypto"
import { readFile } from "node:fs/promises"

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Template = { spec: { label: string } }
type Imported = { id: string; note: { note_type: string } | null }

const TEMPLATE = new URL(
  "../../src/components/settings/noteTypes/templates/psychiatric_follow_up.json",
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
      "Interval history: Mood steadier since the dose increase; back at work full time.",
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
    await expect(note.getByText("Interval history", { exact: true })).toBeVisible()
    await expect(note.getByText("Medical decision making", { exact: true })).toBeVisible()
  } finally {
    await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
})
