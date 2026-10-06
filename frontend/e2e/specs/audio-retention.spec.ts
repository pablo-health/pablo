// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice set to delete session audio when the note is signed.
 *
 * The setting is chosen in Settings and read back after a reload; the script
 * read aloud before recording then says when the audio goes. A clip dictated
 * into an unsigned note is still in storage while the note is open, because a
 * redraft and "Dictate more" use it, and is gone once the note is signed.
 *
 * Storage is the stack's object store, listed directly: the backend never
 * says what it keeps, so the bucket is the only honest witness. The
 * microphone is Chromium's fake device, as in dictate-more.spec.ts.
 *
 * In a practice of its own (fixtures/freshPractice.ts): deleting on signing
 * would reach every other spec's recordings in the shared practice.
 */

import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice, type FreshPractice } from "../fixtures/freshPractice"
import { givePatient, giveTranscribedSession } from "../fixtures/scenarios"
import { STORAGE_URL } from "../fixtures/stack"

test.describe.configure({ mode: "serial", retries: 0 })

test.use({
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  },
})

/** The bucket session audio goes to (backend settings default). */
const AUDIO_BUCKET = "pablo-audio"
const SIGNER = "Sam Ortiz"

/** Names of the objects stored for this session's dictated clips. */
async function storedClips(sessionId: string): Promise<string[]> {
  const prefix = encodeURIComponent(`dictations/${sessionId}/`)
  const response = await fetch(`${STORAGE_URL}/storage/v1/b/${AUDIO_BUCKET}/o?prefix=${prefix}`)
  expect(response.ok).toBe(true)
  const body = (await response.json()) as { items?: { name: string }[] }
  return (body.items ?? []).map((item) => item.name)
}

let practice: FreshPractice

test.beforeAll(async ({ browser }) => {
  practice = await signInToFreshPractice(browser, "retention")
  await practice.context.grantPermissions(["microphone"])
  const { api } = practice
  await api.put("/api/users/me/practice/ai-notes-consent", { ask_clients_about_ai_notes: true })
  // Today, not the calendar's first-run steps, is where the script is offered.
  const preferences = await api.get<Record<string, unknown>>("/api/users/me/preferences")
  await api.put("/api/users/me/preferences", { ...preferences, calendar_setup_complete: true })
})

test.afterAll(async () => {
  await practice?.context.close()
})

test("the practice chooses to delete audio when the note is signed", async () => {
  const { page } = practice
  await page.goto("/dashboard/settings/sessions")

  const onSigning = page.getByRole("radio", { name: "Delete when the note is signed" })
  await expect(page.getByRole("radio", { name: "Delete after" })).toBeChecked()
  await expect(page.getByRole("spinbutton", { name: "Days to keep session audio" })).toHaveValue(
    "365",
  )

  await onSigning.check()
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/users/me/practice/audio-retention") &&
      r.request().method() === "PUT" &&
      r.ok(),
  )
  await page.getByRole("button", { name: "Save" }).click()
  expect((await (await saved).json()).audio_retention_days).toBe(0)
  await expect(page.getByRole("status")).toHaveText("Saved")

  await page.reload()
  await expect(onSigning).toBeChecked()
})

test("a dictated clip stays while the note is open and is gone once it is signed", async () => {
  const { page, api } = practice
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

  // The script read before recording says when the audio goes.
  await page.goto("/dashboard")
  await page.getByRole("button", { name: "Consent script" }).click()
  await expect(
    page.getByRole("dialog", { name: "Asking about AI-assisted notes" }).getByTestId(
      "ai-notes-consent-script",
    ),
  ).toContainText("The audio is deleted once your note is signed.")
  await page.keyboard.press("Escape")

  // Dictate into the open note; the redraft uses the clip.
  await page.goto(`/dashboard/sessions/${session.id}`)
  const body = page.getByTestId("session-note")
  const panel = page.getByRole("region", { name: "Dictate more" })
  await panel.getByRole("button", { name: "Dictate more" }).click()
  await expect(panel.getByText(/Recording 0:0[1-9]/)).toBeVisible({ timeout: 10_000 })
  await panel.getByRole("button", { name: "Stop" }).click()
  await panel.getByRole("button", { name: "Add to note" }).click()
  await expect(body.getByText(/Two weeks from today, same time\./)).toBeVisible({
    timeout: 30_000,
  })

  // Unsigned: the clip is still there.
  expect(await storedClips(session.id)).toHaveLength(1)

  await page.getByRole("button", { name: "Sign and lock" }).click()
  const signDialog = page.getByRole("dialog", { name: "Sign and lock note" })
  await signDialog.getByLabel("Your name").fill(SIGNER)
  const signed = page.waitForResponse(
    (r) => r.url().endsWith(`/api/sessions/${session.id}/finalize`) && r.ok(),
  )
  await signDialog.getByRole("button", { name: "Sign and lock" }).click()
  await signed

  // Signed: the clip is gone, and the note is signed.
  expect(await storedClips(session.id)).toEqual([])
  const note = await api.get<{ note: { finalized_at: string | null } }>(
    `/api/sessions/${session.id}`,
  )
  expect(note.note.finalized_at).not.toBeNull()
})
