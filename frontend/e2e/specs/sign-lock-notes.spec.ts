// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A clinician signs and locks a drafted note, adds to it, unlocks it to
 * correct an error, and signs it again — and the PDF says all of it.
 *
 * The session and its draft come through the API (the stack's drafting
 * stand-in writes the note). Everything after that is driven the way a
 * clinician does it, and the PDF is read from the bytes the browser saved.
 */

import { readFile } from "node:fs/promises"
import type { Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { givePatient, giveTranscribedSession } from "../fixtures/scenarios"

const isNoteRequest = (suffix: string, method = "POST") => (response: {
  url: () => string
  request: () => { method: () => string }
  ok: () => boolean
}) =>
  new RegExp(`/api/notes/[0-9a-f-]+${suffix}$`).test(response.url()) &&
  response.request().method() === method &&
  response.ok()

const SIGNER = "Sam Ortiz"

async function signInDialog(page: Page, credentials: string) {
  const dialog = page.getByRole("dialog", { name: "Sign and lock note" })
  await dialog.getByLabel("Your name").fill(SIGNER)
  await dialog.getByLabel("Credentials").fill(credentials)
  await expect(dialog.getByTestId("signature-preview")).toContainText(
    `Electronically signed by ${SIGNER}, ${credentials}`,
  )
  await dialog.getByRole("button", { name: "Sign and lock" }).click()
  await expect(dialog).toBeHidden()
}

test("sign and lock a note, add to it, unlock it with a reason and sign it again", async ({
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

  // Sign and lock, with credentials edited for this signature only.
  await page.goto(`/dashboard/sessions/${session.id}`)
  await page.getByRole("button", { name: "Sign and lock" }).click()
  const finalized = page.waitForResponse(
    (r) => r.url().endsWith(`/api/sessions/${session.id}/finalize`) && r.ok(),
  )
  await signInDialog(page, `LMFT ${marker}`)
  await finalized

  // A reload shows the block and a read-only note offering only addendum and unlock.
  await page.reload()
  const block = page.getByTestId("signature-block")
  await expect(block).toContainText(`Electronically signed by ${SIGNER}, LMFT ${marker}`)
  await expect(page.getByRole("button", { name: /^Edit$/ })).toHaveCount(0)
  const panel = page.getByRole("region", { name: "Signature" })
  await expect(panel.getByRole("button", { name: "Add addendum" })).toBeVisible()
  await expect(panel.getByRole("button", { name: "Unlock" })).toBeVisible()

  // An addendum, signed.
  const addendumText = `Client called after the session ${marker}`
  await panel.getByRole("button", { name: "Add addendum" }).click()
  const addendumDialog = page.getByRole("dialog", { name: "Add addendum" })
  await addendumDialog.getByLabel("Addendum").fill(addendumText)
  await addendumDialog.getByLabel("Your name").fill(SIGNER)
  const added = page.waitForResponse(isNoteRequest("/addenda"))
  await addendumDialog.getByRole("button", { name: "Sign and add" }).click()
  await added
  await expect(panel.getByText(addendumText)).toBeVisible()

  // Unlock to correct an error, with a reason.
  const reason = `Wrong date of service ${marker}`
  await panel.getByRole("button", { name: "Unlock" }).click()
  const unlockDialog = page.getByRole("dialog", { name: "Unlock note" })
  await expect(unlockDialog.getByRole("button", { name: "Accept & unlock" })).toBeDisabled()
  await unlockDialog.getByLabel("Reason for unlocking").fill(reason)
  const unlocked = page.waitForResponse(isNoteRequest("/unlock"))
  await unlockDialog.getByRole("button", { name: "Accept & unlock" }).click()
  await unlocked

  // Edit the note, then sign it again.
  const correction = `Practice paced breathing ${marker}`
  await expect(unlockDialog).toBeHidden()
  await page.getByRole("button", { name: /^Edit$/ }).click()
  await page.getByLabel("Next Steps").fill(correction)
  const saved = page.waitForResponse(isNoteRequest("", "PATCH"))
  await page.getByRole("button", { name: "Save Changes" }).click()
  await saved
  await panel.getByRole("button", { name: "Sign and lock" }).click()
  const resigned = page.waitForResponse(isNoteRequest("/sign"))
  await signInDialog(page, `LMFT, LPCC ${marker}`)
  await resigned

  // Two signed versions; the first carries its reason. The addendum stayed.
  await page.reload()
  await expect(block).toContainText(`LMFT, LPCC ${marker}`)
  await panel.getByText("Signed versions (2)").click()
  const first = panel.getByRole("listitem").filter({ hasText: "Version 1" })
  await expect(first).toContainText(`LMFT ${marker}`)
  await expect(first).toContainText(reason)
  await expect(panel.getByText(addendumText)).toBeVisible()

  // The PDF carries the block, the addendum and the amendment line.
  const download = page.waitForEvent("download")
  await page.getByRole("button", { name: "Export PDF" }).click()
  const pdf = (await readFile(await (await download).path())).toString("latin1")
  expect(pdf).toContain(correction)
  expect(pdf).toContain(`Electronically signed by ${SIGNER}, LMFT, LPCC ${marker}`)
  expect(pdf).toContain(addendumText)
  expect(pdf).toMatch(new RegExp(`Amended [A-Z][a-z]{2} \\d{1,2}, \\d{4}: ${reason}`))
})
