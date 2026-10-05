// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Asking clients about AI-assisted notes, a practice setting that starts on.
 *
 * On: Today offers a script to read before recording, carrying the practice's
 * audio retention window, and a session note shows the client's answer from
 * their consent record — "No consent on file" until one is recorded, then the
 * dated answer. Off, in Settings: neither the script nor the line.
 *
 * Every worker shares one practice, so the setting is put back on afterwards
 * whatever happens.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient, giveTranscribedSession } from "../fixtures/scenarios"

const SETTING = "/api/users/me/practice/ai-notes-consent"

interface Setting {
  ask_clients_about_ai_notes: boolean
  audio_retention_days: number
}

/** Today as the consent line shows it, e.g. "Oct 6, 2026". */
function todayShown(): string {
  return new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
}

/** The retention window as the script says it. */
function retentionSaid(days: number): string {
  if (days % 365 === 0) return days === 365 ? "1 year" : `${days / 365} years`
  return `${days} days`
}

test("the practice asks clients about AI-assisted notes, and can stop asking", async ({
  signedInPage: page,
  api,
}) => {
  const setting = await api.put<Setting>(SETTING, { ask_clients_about_ai_notes: true })
  try {
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

    // The script, from Today, with this practice's retention window.
    await page.goto("/dashboard")
    await page.getByRole("button", { name: "Consent script" }).click()
    const scriptDialog = page.getByRole("dialog", { name: "Asking about AI-assisted notes" })
    await expect(scriptDialog.getByTestId("ai-notes-consent-script")).toContainText(
      `The audio is kept for up to ${retentionSaid(setting.audio_retention_days)}.`,
    )
    await page.keyboard.press("Escape")

    // The note: nothing on file, then the answer recorded from the note itself.
    await page.goto(`/dashboard/sessions/${session.id}`)
    const line = page.getByTestId("note-consent-line")
    await expect(line).toHaveText(/No consent on file/)
    await line.getByRole("button", { name: "Record consent" }).click()
    const consentDialog = page.getByRole("dialog", { name: "AI notes" })
    await consentDialog.getByLabel("Client agreed").check()
    await consentDialog.getByRole("button", { name: "Save" }).click()
    await expect(consentDialog).toBeHidden()
    await expect(line).toHaveText(`Client agreed to AI-assisted notes on ${todayShown()}`)

    await page.reload()
    await expect(line).toHaveText(`Client agreed to AI-assisted notes on ${todayShown()}`)

    // Off, in Settings: the line and the script are gone.
    await page.goto("/dashboard/settings/sessions")
    const toggle = page.getByRole("switch", { name: "Ask clients to agree to AI-assisted notes" })
    await expect(toggle).toHaveAttribute("aria-checked", "true")
    const saved = page.waitForResponse(
      (response) =>
        response.url().endsWith(SETTING) && response.request().method() === "PUT" && response.ok(),
    )
    await toggle.click()
    await saved
    await expect(toggle).toHaveAttribute("aria-checked", "false")
    await page.reload()
    await expect(toggle).toHaveAttribute("aria-checked", "false")

    await page.goto(`/dashboard/sessions/${session.id}`)
    await expect(page.getByTestId("soap-pane")).toContainText("Stand-in draft")
    await expect(page.getByTestId("note-consent-line")).toHaveCount(0)

    await page.goto("/dashboard")
    await expect(page.getByRole("heading", { name: "Today" })).toBeVisible()
    await expect(page.getByRole("button", { name: "Consent script" })).toHaveCount(0)

    // The answer itself is still on the chart.
    await page.goto(`/dashboard/patients/${patient.id}`)
    await expect(page.getByTestId("ai-consent-line")).toHaveText(`AI notes: agreed ${todayShown()}`)
  } finally {
    await api.put<Setting>(SETTING, { ask_clients_about_ai_notes: true })
  }
})
