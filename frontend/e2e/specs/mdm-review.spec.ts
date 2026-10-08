// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Medical decision making on a psychiatric follow-up, reviewed beside the
 * note: the clinician picks the three levels and sees the E/M code; once the
 * note has a psychotherapy portion, time is no longer offered as a way to
 * choose the level, and the add-on follows the confirmed minutes. The codes
 * go into the note's visit details only when the clinician asks.
 *
 * The stack drafts through its stand-in (scripts/fake_llm.py): every field
 * reads "Stand-in draft for <section>.<field>.", so the visit details state
 * no code and the medical decision making evidence is easy to find.
 */

import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

// A medication check, then therapy from 12:30, the client leaving at 50:00.
const VISIT = [
  "[00:00:05] Therapist: How has the medication been since the dose change?",
  "[00:03:00] Client: Better sleep, and no side effects I have noticed.",
  "[00:11:50] Therapist: Good, we will keep the dose where it is for now.",
  "[00:12:30] Therapist: Now let's get into the session work you wanted.",
  "[00:30:00] Client: I keep replaying the argument with my sister every night.",
  "[00:50:00] Client: Thank you, see you next month.",
  "[00:51:00] Therapist: Addendum for the note. Client denies suicidal ideation.",
].join("\n")

type Sections = Record<string, Record<string, unknown>>
type Session = { id: string; status: string }
type SessionNote = { note: { id: string; content: Sections; content_edited: Sections | null } }

async function recordedFollowUp(api: ApiClient): Promise<Session> {
  const patient = await givePatient(api)
  const session = await api.post<Session>("/api/sessions/schedule", {
    patient_id: patient.id,
    scheduled_at: new Date().toISOString(),
    source: "companion",
    video_platform: "zoom",
    note_type: "psychiatric_follow_up",
    note_inputs: { place_of_service: "In office" },
  })
  await api.patch(`/api/sessions/${session.id}/status`, { status: "in_progress" })
  await api.patch(`/api/sessions/${session.id}/status`, { status: "recording_complete" })
  await api.post(`/api/sessions/${session.id}/transcript`, { format: "txt", content: VISIT })
  await expect
    .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status, {
      timeout: 30_000,
    })
    .toBe("pending_review")
  return session
}

test("the clinician picks the MDM levels, then a psychotherapy portion brings the add-on", async ({
  api,
  signedInPage: page,
}) => {
  const session = await recordedFollowUp(api)
  // Start from a medication-only visit: the stand-in writes every field, so
  // its psychotherapy section is emptied as the clinician's own edit.
  const { note } = await api.get<SessionNote>(`/api/sessions/${session.id}`)
  const psychotherapy = Object.fromEntries(
    Object.keys(note.content.psychotherapy).map((key) => [key, ""]),
  )
  await api.patch(`/api/notes/${note.id}`, {
    content_edited: { ...note.content, psychotherapy },
  })

  await page.goto(`/dashboard/sessions/${session.id}`)
  const panel = page.getByTestId("mdm-review")
  const body = page.getByTestId("session-note")

  // The evidence is drafted for the panel, never for the note.
  await expect(panel.getByText("Stand-in draft for mdm.problems_addressed.")).toBeVisible()
  await expect(body.getByRole("heading", { name: "Medical decision making" })).toHaveCount(0)
  await expect(body.getByText("Stand-in draft for mdm.problems_addressed.")).toHaveCount(0)

  // Without psychotherapy, time is a way to choose the level.
  await expect(panel.getByTestId("mdm-billing-time")).toBeVisible()

  const choose = async (label: string, level: string) => {
    const saved = page.waitForResponse(
      (r) => r.url().endsWith(`/api/notes/${note.id}/mdm`) && r.request().method() === "PUT",
    )
    await panel.getByLabel(label).selectOption(level)
    expect((await saved).status()).toBe(200)
  }
  await choose("Problems addressed", "moderate")
  await choose("Risk of management", "moderate")
  await choose("Data reviewed", "limited")

  await expect(panel.getByTestId("mdm-level")).toHaveText("Moderate")
  await expect(panel.getByTestId("mdm-em-code-value")).toHaveText("99214")
  await expect(panel.getByTestId("mdm-element-data").getByTestId("mdm-meets")).toHaveText(
    "Below moderate; the other two set the level",
  )
  // Choosing a level drafts nothing again.
  await expect(page.getByText("Redrafting the note…")).toHaveCount(0)
  expect((await api.get<SessionNote>(`/api/sessions/${session.id}`)).note.content).toEqual(
    note.content,
  )

  // The clinician writes the therapy portion into the note.
  await body.getByRole("button", { name: /edit/i }).click()
  await body.getByLabel("Issues addressed").fill("Rumination about a family argument.")
  await body.getByLabel("Modality and interventions").fill("Cognitive restructuring.")
  await body.getByRole("button", { name: /save changes/i }).click()

  // With psychotherapy, the level is chosen by MDM alone, and the add-on
  // waits for confirmed minutes.
  await expect(panel.getByTestId("mdm-billing-time")).toHaveCount(0)
  await expect(panel.getByTestId("mdm-add-on")).toHaveText(
    "Confirm the psychotherapy minutes to see it",
  )

  await page.getByRole("radio", { name: "Type the minutes" }).check()
  await page.getByLabel("Psychotherapy minutes").fill("20")
  await page.getByRole("button", { name: "Confirm" }).click()
  await expect(page.getByTestId("psychotherapy-confirmed")).toHaveText("20 minutes")

  await expect(panel.getByTestId("mdm-add-on")).toHaveText("90833 · 20 confirmed minutes")
  await expect(panel.getByTestId("mdm-em-code-value")).toHaveText("99214")

  // The note states no code until the clinician puts them in.
  await panel.getByRole("button", { name: "Add 99214 and 90833 to the note" }).click()
  await expect(body.getByText(/E\/M code: 99214/)).toBeVisible()
  await expect(body.getByText(/Psychotherapy add-on code: 90833/)).toBeVisible()
  await expect(panel.getByRole("button", { name: /to the note/ })).toHaveCount(0)
  const saved = await api.get<SessionNote>(`/api/sessions/${session.id}`)
  const details = String(saved.note.content_edited?.encounter?.visit_details)
  expect(details).toContain("E/M code: 99214")
  expect(details).toContain("Psychotherapy add-on code: 90833")
})
