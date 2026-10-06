// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The clinician edits one field of a SOAP section, then dictates something
 * that belongs in another field of the same section. Redrafting with their
 * edits kept leaves the edited field as they wrote it and puts the dictation
 * in its own field.
 *
 * The stack's stand-in (scripts/fake_llm.py) hears every clip as "Next
 * session: Two weeks from today, same time." and drafts it into the Plan's
 * next session; the clinician's edit is to the Plan's next steps.
 */

import type { Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { givePatient, giveTranscribedSession } from "../fixtures/scenarios"

test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  },
})

async function dictateIntoTheNote(page: Page) {
  const panel = page.getByRole("region", { name: "Dictate more" })
  await panel.getByRole("button", { name: "Dictate more" }).click()
  await expect(panel.getByText(/Recording 0:0[1-9]/)).toBeVisible({ timeout: 10_000 })
  await panel.getByRole("button", { name: "Stop" }).click()
  await panel.getByRole("button", { name: "Add to note" }).click()
}

test("a dictation lands in its own field of a section the clinician edited", async ({
  signedInPage: page,
  api,
}) => {
  const mine = `Walk every morning ${Date.now().toString(36)}`
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

  // Edit one Plan field.
  await page.getByRole("button", { name: /^Edit$/ }).click()
  await page.getByLabel("Next Steps").fill(mine)
  await page.getByRole("button", { name: "Save Changes" }).click()
  await expect(body.getByText(mine)).toBeVisible()

  // Dictate for another Plan field, keeping the edits (the default).
  await dictateIntoTheNote(page)
  const choice = page.getByRole("dialog", { name: "You've edited this note" })
  await expect(choice.getByRole("radio", { name: /Keep my edits/ })).toBeChecked()
  await choice.getByRole("button", { name: "Add to note" }).click()

  await expect(body.getByText(/Two weeks from today, same time\./)).toBeVisible({
    timeout: 30_000,
  })
  await expect(body.getByText(mine)).toBeVisible()
  await expect(body.getByText(/Stand-in draft for plan\.next_session/)).toHaveCount(0)
  await expect(body.getByText(/Stand-in draft for plan\.next_steps/)).toHaveCount(0)

  // Still so after a reload: it is what was saved.
  await page.reload()
  await expect(body.getByText(/Two weeks from today, same time\./)).toBeVisible()
  await expect(body.getByText(mine)).toBeVisible()
})
