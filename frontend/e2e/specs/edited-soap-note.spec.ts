// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A clinician edits a drafted SOAP note: the note shows the edit, not the
 * draft — on the session page as soon as it is saved, on the note's own page
 * after a reload, back on the session page, and in the PDF.
 *
 * The session and its draft come through the API (the stack's drafting
 * stand-in writes every field as "Stand-in draft for <field>."). The edits,
 * the reloads and the export are driven the way a clinician does them.
 */

import { readFile } from "node:fs/promises"
import type { Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { givePatient, giveTranscribedSession } from "../fixtures/scenarios"

const DRAFTED_NEXT_STEPS = /Stand-in draft for plan\.next_steps/

async function editNextSteps(page: Page, text: string) {
  await page.getByRole("button", { name: /^Edit$/ }).click()
  await page.getByLabel("Next Steps").fill(text)
  await page.getByRole("button", { name: "Save Changes" }).click()
}

async function expectShowsEdit(page: Page, text: string) {
  await expect(page.getByText(text)).toBeVisible()
  await expect(page.getByText("Edited", { exact: true })).toBeVisible()
  await expect(page.getByText(DRAFTED_NEXT_STEPS)).toHaveCount(0)
}

test("an edited drafted SOAP note shows the edit, not the draft, on screen and in the PDF", async ({
  signedInPage: page,
  api,
}) => {
  const marker = `e2e-${Date.now().toString(36)}`
  const patient = await givePatient(api)
  const session = await giveTranscribedSession(
    api,
    patient.id,
    "[00:00:05] Therapist: How was the week?\n[00:00:09] Client: Better than the last one.",
  )
  await expect
    .poll(async () => (await api.get<{ status: string }>(`/api/sessions/${session.id}`)).status, {
      timeout: 30_000,
    })
    .toBe("pending_review")
  const { note } = await api.get<{ note: { id: string } }>(`/api/sessions/${session.id}`)

  // The draft, as generated; an edit replaces it on screen at once.
  await page.goto(`/dashboard/sessions/${session.id}`)
  await expect(page.getByText(DRAFTED_NEXT_STEPS)).toBeVisible()
  await expect(page.getByText("AI Generated", { exact: true })).toBeVisible()
  await editNextSteps(page, `Practice paced breathing ${marker}`)
  await expectShowsEdit(page, `Practice paced breathing ${marker}`)

  // On the note's own page an edit is saved to the note, so a reload keeps it.
  const correction = `Walk daily ${marker}`
  await page.goto(`/dashboard/patients/${patient.id}/notes/${note.id}`)
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/api/notes/${note.id}`) && r.request().method() === "PATCH" && r.ok(),
  )
  await editNextSteps(page, correction)
  await saved
  await page.reload()
  await expectShowsEdit(page, correction)

  // The PDF says what the screen says.
  const download = page.waitForEvent("download")
  await page.getByRole("button", { name: "Export PDF" }).click()
  const pdf = (await readFile(await (await download).path())).toString("latin1")
  expect(pdf).toContain(correction)
  expect(pdf).not.toMatch(DRAFTED_NEXT_STEPS)

  // And the session page shows the saved edit too.
  await page.goto(`/dashboard/sessions/${session.id}`)
  await expectShowsEdit(page, correction)
})
