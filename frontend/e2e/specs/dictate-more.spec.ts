// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * After the recording stops, the clinician dictates more. An unsigned note
 * is redrafted with it; once the note is signed, a dictation becomes a draft
 * addendum the clinician reviews and signs, and the PDF carries it.
 *
 * The browser's microphone is Chromium's fake device (a tone), so the clip is
 * real audio a real MediaRecorder made. The stack's stand-in
 * (scripts/fake_llm.py) hears every clip as DICTATED, and its drafting
 * stand-in puts that line in the field it names — "Next session" — so the
 * redraft visibly gains it.
 */

import { readFile } from "node:fs/promises"
import type { Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { givePatient, giveTranscribedSession } from "../fixtures/scenarios"

const DICTATED = "Next session: Two weeks from today, same time."
const SIGNER = "Sam Ortiz"

test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  },
})

async function dictate(page: Page, action: "Add to note" | "Draft an addendum") {
  const panel = page.getByRole("region", { name: "Dictate more" })
  await panel.getByRole("button", { name: "Dictate more" }).click()
  await expect(panel.getByText(/Recording 0:0[1-9]/)).toBeVisible({ timeout: 10_000 })
  await panel.getByRole("button", { name: "Stop" }).click()
  const sent = page.waitForResponse(
    (r) => /\/api\/sessions\/[\w-]+\/dictations$/.test(r.url()) && r.request().method() === "POST",
  )
  await panel.getByRole("button", { name: action }).click()
  expect((await sent).status()).toBe(202)
}

test("dictate more: an unsigned note is redrafted, a signed one gets an addendum", async ({
  signedInPage: page,
  api,
}) => {
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

  await page.goto(`/dashboard/sessions/${session.id}`)
  const body = page.getByTestId("session-note")
  await expect(body.getByText(/Stand-in draft for plan\.next_session/)).toBeVisible()

  // Unsigned: the note is redrafted with what was dictated.
  await dictate(page, "Add to note")
  await expect(body.getByText(/Two weeks from today, same time\./)).toBeVisible({
    timeout: 30_000,
  })
  await expect(body.getByText(/Stand-in draft for plan\.next_session/)).toHaveCount(0)

  // Sign it.
  await page.getByRole("button", { name: "Sign and lock" }).click()
  const signDialog = page.getByRole("dialog", { name: "Sign and lock note" })
  await signDialog.getByLabel("Your name").fill(SIGNER)
  const finalized = page.waitForResponse(
    (r) => r.url().endsWith(`/api/sessions/${session.id}/finalize`) && r.ok(),
  )
  await signDialog.getByRole("button", { name: "Sign and lock" }).click()
  await finalized
  await page.reload()
  const signedBody = await body.innerText()

  // Signed: the dictation becomes a draft addendum, and the body stays as signed.
  await dictate(page, "Draft an addendum")
  const draft = page.getByTestId("draft-addendum")
  await expect(draft).toContainText(DICTATED, { timeout: 30_000 })
  expect(await body.innerText()).toBe(signedBody)

  await draft.getByRole("button", { name: "Review and sign" }).click()
  const addendumDialog = page.getByRole("dialog", { name: "Add addendum" })
  await expect(addendumDialog.getByLabel("Addendum")).toHaveValue(DICTATED)
  await addendumDialog.getByLabel("Your name").fill(SIGNER)
  const added = page.waitForResponse(
    (r) => /\/api\/notes\/[\w-]+\/addenda$/.test(r.url()) && r.ok(),
  )
  await addendumDialog.getByRole("button", { name: "Sign and add" }).click()
  await added

  const panel = page.getByRole("region", { name: "Signature" })
  await expect(panel.getByRole("listitem").filter({ hasText: DICTATED })).toBeVisible()
  await expect(draft).toHaveCount(0)
  expect(await body.innerText()).toBe(signedBody)

  // The PDF carries the addendum.
  const download = page.waitForEvent("download")
  await page.getByRole("button", { name: "Export PDF" }).click()
  const pdf = (await readFile(await (await download).path())).toString("latin1")
  expect(pdf).toContain(DICTATED)
  expect(pdf).toContain(`Electronically signed by ${SIGNER}`)
})
