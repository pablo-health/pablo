// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A note's PDF is the record, so it carries what the note says and nothing
 * standing in for a gap. A psychiatric follow-up drafted for a client whose
 * chart is empty has whole sections with nothing in them: on screen each
 * shows a quiet "Nothing entered", and the PDF leaves the section out,
 * heading included, along with every empty field's label. What the visit did
 * cover still prints as written, the visit's marks included.
 *
 * Catches a PDF that prints "No content" (or a bare heading) for an empty
 * section, a screen cue that leaks into the PDF, and a fix that strips the
 * visit's own "(not asked this visit)" marks along with the filler.
 *
 * The stack drafts through its stand-in (scripts/fake_llm.py), as in
 * drafted-note-empty-chart.spec.ts. jsPDF writes its text uncompressed with
 * "(" and ")" escaped, so the PDF checks avoid parentheses.
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
const EMPTY_SECTIONS = [
  "Medications and allergies",
  "Psychiatric history",
  "Trauma history",
  "Medical history",
  "Family history",
]

type Session = { id: string; status: string }

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

test("a note's PDF leaves out empty sections and fields, and the screen marks them quietly", async ({
  api,
  signedInPage: page,
}) => {
  const session = await draftedFollowUp(api)

  await page.goto(`/dashboard/sessions/${session.id}`)
  const body = page.getByTestId("session-note")
  await expect(body.getByText("Stand-in draft for subjective.chief_complaint.")).toBeVisible()
  const section = (label: string) =>
    body.getByRole("heading", { level: 4, name: label, exact: true }).locator("xpath=..")

  // On screen: each empty section is marked, never filled with note-like words.
  for (const label of EMPTY_SECTIONS) {
    await expect(section(label).getByText("Nothing entered", { exact: true }), label).toBeVisible()
  }
  await expect(body).not.toContainText("No content")

  const download = page.waitForEvent("download")
  await body.getByRole("button", { name: "Export PDF" }).click()
  const pdf = (await readFile(await (await download).path())).toString("latin1")

  // What the note says prints, the visit's marks included.
  expect(pdf).toContain("Stand-in draft for subjective.chief_complaint.")
  expect(pdf).toContain("Substance use")
  expect(pdf).toContain("not asked this visit")
  expect(pdf).toContain("Separated in the spring.")

  // Nothing stands in for what it doesn't: no filler, no screen cue, no
  // heading over an empty section, no label over an empty field.
  expect(pdf).not.toContain("No content")
  expect(pdf).not.toContain("Nothing entered")
  for (const label of EMPTY_SECTIONS) {
    expect(pdf, `PDF prints the empty section "${label}"`).not.toContain(label)
  }
  expect(pdf).not.toContain("Supports:")
  expect(pdf).not.toContain("Allergies:")
})
