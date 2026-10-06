// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A clinician edits a drafted SOAP note on the session page and the edit is
 * saved to the note there and then: it survives a reload, a dictation that
 * redrafts the note while keeping edits, and signing — the signed version
 * holds the edit, and so does the PDF.
 *
 * The session and its draft come through the API (the stack's drafting
 * stand-in writes every field as "Stand-in draft for <field>."). The browser's
 * microphone is Chromium's fake device, and the stand-in hears every clip as
 * DICTATED and puts it in "Next session". Everything after the draft is
 * driven the way a clinician does it.
 */

import { readFile } from "node:fs/promises"
import { expect, test } from "../fixtures/auth"
import { givePatient, giveTranscribedSession } from "../fixtures/scenarios"

// The edit is to a field outside the plan, where the dictation lands: a
// SOAP edit is kept a section at a time.
const DRAFTED_PROGRESS = /Stand-in draft for assessment\.progress/
const SIGNER = "Sam Ortiz"

test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  },
})

type SigningRecord = { versions: { content_edited: Record<string, unknown> | null }[] }

test("an edit made on the session page survives a reload, a redraft and signing", async ({
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

  // Edit on the session page: the edit is saved to the note at once.
  await page.goto(`/dashboard/sessions/${session.id}`)
  const body = page.getByTestId("session-note")
  await expect(body.getByText(DRAFTED_PROGRESS)).toBeVisible()
  const mine = `Sleeping through the night ${marker}`
  await page.getByRole("button", { name: /^Edit$/ }).click()
  await page.getByLabel("Progress", { exact: true }).fill(mine)
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/api/notes/${note.id}`) && r.request().method() === "PATCH" && r.ok(),
  )
  await page.getByRole("button", { name: "Save Changes" }).click()
  await saved

  // A reload reads it back from the note.
  await page.reload()
  await expect(body.getByText(mine)).toBeVisible()
  await expect(body.getByText(DRAFTED_PROGRESS)).toHaveCount(0)

  // Dictate more: the redraft asks about the edit, keeps it by default, and
  // the dictation lands in the rest of the note.
  const dictation = page.getByRole("region", { name: "Dictate more" })
  await dictation.getByRole("button", { name: "Dictate more" }).click()
  await expect(dictation.getByText(/Recording 0:0[1-9]/)).toBeVisible({ timeout: 10_000 })
  await dictation.getByRole("button", { name: "Stop" }).click()
  await dictation.getByRole("button", { name: "Add to note" }).click()
  const choice = page.getByRole("dialog", { name: "You've edited this note" })
  await expect(choice.getByRole("radio", { name: /Keep my edits/ })).toBeChecked()
  await choice.getByRole("button", { name: "Add to note" }).click()
  await expect(body.getByText(/Two weeks from today, same time\./)).toBeVisible({
    timeout: 30_000,
  })
  await expect(body.getByText(mine)).toBeVisible()
  await page.reload()
  await expect(body.getByText(mine)).toBeVisible()

  // Sign and lock: the signed version holds the edit.
  await page.getByRole("button", { name: "Sign and lock" }).click()
  const signDialog = page.getByRole("dialog", { name: "Sign and lock note" })
  await signDialog.getByLabel("Your name").fill(SIGNER)
  const finalized = page.waitForResponse(
    (r) => r.url().endsWith(`/api/sessions/${session.id}/finalize`) && r.ok(),
  )
  await signDialog.getByRole("button", { name: "Sign and lock" }).click()
  await finalized
  await page.reload()
  await expect(page.getByTestId("signature-block")).toContainText(
    `Electronically signed by ${SIGNER}`,
  )
  await expect(body.getByText(mine)).toBeVisible()
  await expect(body.getByText(DRAFTED_PROGRESS)).toHaveCount(0)

  const signing = await api.get<SigningRecord>(`/api/notes/${note.id}/signing`)
  expect(signing.versions).toHaveLength(1)
  expect(JSON.stringify(signing.versions[0].content_edited)).toContain(mine)

  const download = page.waitForEvent("download")
  await page.getByRole("button", { name: "Export PDF" }).click()
  const pdf = (await readFile(await (await download).path())).toString("latin1")
  expect(pdf).toContain(mine)
  expect(pdf).not.toMatch(DRAFTED_PROGRESS)
})
