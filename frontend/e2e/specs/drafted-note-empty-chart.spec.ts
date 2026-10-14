// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A psychiatric follow-up drafted for a client whose chart is empty: every
 * box the chart backs is empty, on screen and in the PDF, with no "Not
 * recorded" or "None recorded" standing in for it. What the visit covered
 * still shows, alone: a substance never asked about reads "(not asked this
 * visit)", one asked about reads "(asked this visit: no change)", and a
 * history answer the client gave is printed as stated this visit.
 *
 * Catches a draft that writes a placeholder where the chart is silent, or
 * drops the visit's marks along with it.
 *
 * The stack drafts through its stand-in (scripts/fake_llm.py). Its
 * extraction call answers "Update on <key>: <text>" with a statement about
 * that field and "No change in <key>." with a substance screen of no
 * change, so every other chart-backed box has nothing from the visit.
 */

import { readFile } from "node:fs/promises"

import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

const VISIT = [
  "[00:00:05] Therapist: How has sleep been since the last visit?",
  "[00:00:09] Client: A little better, most nights.",
  "[00:00:15] Client: Update on relationships: Separated in the spring.",
  "[00:00:21] Client: No change in alcohol.",
].join("\n")

/** Sections whose every field the chart backs: empty on an empty chart. */
const CHART_ONLY_SECTIONS = [
  "Medications and allergies",
  "Psychiatric history",
  "Trauma history",
  "Medical history",
  "Family history",
]

const PLACEHOLDER = /not recorded|none recorded/i

type Sections = Record<string, Record<string, unknown>>
type Session = { id: string; status: string }
type SessionNote = { note: { id: string; content: Sections } }

async function draftedFollowUp(api: ApiClient): Promise<Session> {
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

test("a draft over an empty chart leaves the chart's boxes empty and keeps the visit's marks", async ({
  api,
  signedInPage: page,
}) => {
  const session = await draftedFollowUp(api)

  // As stored: empty where Pablo has nothing, the mark alone where the visit said something.
  const { note } = await api.get<SessionNote>(`/api/sessions/${session.id}`)
  expect(note.content.medications).toEqual({ current_medications: [], allergies: "" })
  expect(note.content.social_history.relationships).toBe(
    '(stated this visit: "Separated in the spring.")',
  )
  expect(note.content.social_history.supports).toBe("")
  expect(note.content.substance_use.alcohol).toBe("(asked this visit: no change)")
  expect(note.content.substance_use.cannabis).toBe("(not asked this visit)")

  await page.goto(`/dashboard/sessions/${session.id}`)
  const body = page.getByTestId("session-note")
  await expect(body.getByText("Stand-in draft for subjective.chief_complaint.")).toBeVisible()
  const section = (label: string) =>
    body.getByRole("heading", { level: 4, name: label, exact: true }).locator("xpath=..")

  for (const label of CHART_ONLY_SECTIONS) {
    await expect(section(label), label).toContainText("Nothing entered")
  }
  const substances = section("Substance use")
  await expect(substances.getByText("(not asked this visit)", { exact: true })).toHaveCount(7)
  await expect(substances.getByText("(asked this visit: no change)", { exact: true })).toBeVisible()
  const social = section("Social history and supports")
  await expect(
    social.getByText('(stated this visit: "Separated in the spring.")', { exact: true }),
  ).toBeVisible()
  await expect(social.getByRole("heading", { name: "Supports", exact: true })).toHaveCount(0)
  await expect(body).not.toContainText(PLACEHOLDER)

  // The PDF prints what the screen shows. jsPDF escapes "(" and ")", so the
  // checks avoid them.
  const download = page.waitForEvent("download")
  await body.getByRole("button", { name: "Export PDF" }).click()
  const pdf = (await readFile(await (await download).path())).toString("latin1")
  expect(pdf).toContain("not asked this visit")
  expect(pdf).toContain("Separated in the spring.")
  expect(pdf).not.toMatch(PLACEHOLDER)
})
