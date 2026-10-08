// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A note of a practice's own type exports as a PDF: a type saved from the
 * psychiatric follow-up template, a recorded visit drafted with it, the
 * psychotherapy minutes confirmed, the note signed — and the PDF carries the
 * type's fields with their values, the visit's times and minutes, and the
 * signature block.
 *
 * The stack drafts through its stand-in (scripts/fake_llm.py): every text
 * field reads "Stand-in draft for <section>.<field>.", a diagnoses field
 * holds one "Stand-in diagnosis for <section>.<field>" coded F00.0, and the
 * turns from the clinician's spoken cue are labeled therapy. The PDF is read from
 * the bytes the browser saved; jsPDF writes its text uncompressed, with "("
 * and ")" escaped, so the checks avoid parentheses.
 */

import { randomBytes } from "node:crypto"
import { readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"

import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

const TEMPLATE = new URL(
  "../../../backend/app/notes/templates/psychiatric_follow_up.json",
  import.meta.url,
)
const SIGNER = "Sam Ortiz"

// The clinician's cue at 12:30 and the client leaving at 50:00: 37 minutes.
const VISIT = [
  "[00:00:05] Therapist: How has the medication been since the dose change?",
  "[00:03:00] Client: Better sleep, and no side effects I have noticed.",
  "[00:11:50] Therapist: Good, we will keep the dose where it is for now.",
  "[00:12:30] Therapist: Now let's get into the session work you wanted.",
  "[00:30:00] Client: I keep replaying the argument with my sister every night.",
  "[00:50:00] Client: Thank you, see you next month.",
  "[00:51:00] Therapist: Addendum for the note. Client denies suicidal ideation.",
].join("\n")

type Session = { id: string; status: string }

async function recordedVisit(api: ApiClient, noteType: string): Promise<Session> {
  const patient = await givePatient(api)
  const session = await api.post<Session>("/api/sessions/schedule", {
    patient_id: patient.id,
    scheduled_at: new Date().toISOString(),
    source: "companion",
    video_platform: "zoom",
    note_type: noteType,
    note_inputs: { place_of_service: "Telehealth" },
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

test("a signed note of a practice's own type exports its fields, visit times and signature", async ({
  signedInPage: page,
  api,
}) => {
  const { spec } = JSON.parse(await readFile(fileURLToPath(TEMPLATE), "utf8")) as { spec: object }
  const slug = `e2e_pdf_${randomBytes(3).toString("hex")}`
  const { key } = await api.put<{ key: string }>(`/api/note-types/custom/${slug}`, spec)
  try {
    const session = await recordedVisit(api, key)
    await page.goto(`/dashboard/sessions/${session.id}`)
    const body = page.getByTestId("session-note")
    await expect(body.getByText("Stand-in draft for subjective.chief_complaint.")).toBeVisible()

    // Confirm the therapy turns as labeled: one run, from the clinician's cue.
    const times = page.getByTestId("visit-times")
    await expect(times.getByTestId("psychotherapy-preview")).toHaveText(/^37 therapy minutes /)
    await times.getByRole("button", { name: "Confirm" }).click()
    const windowText = await page.getByTestId("psychotherapy-confirmed").innerText()
    expect(windowText).toMatch(/^\d{1,2}:\d{2} [AP]M to \d{1,2}:\d{2} [AP]M, 37 minutes$/)
    const visitLine = await times.getByTestId("visit-line").innerText()

    // Sign and lock.
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

    const download = page.waitForEvent("download")
    await body.getByRole("button", { name: "Export PDF" }).click()
    const saved = await download
    expect(saved.suggestedFilename()).toMatch(/^psychiatric-follow-up-e-m-psychotherapy-.+\.pdf$/)
    const pdf = (await readFile(await saved.path())).toString("latin1")

    // The type's fields, labelled, with their values, in the type's order.
    const at = (text: string) => {
      const index = pdf.indexOf(text)
      expect(index, `PDF is missing "${text}"`).toBeGreaterThan(-1)
      return index
    }
    expect(at("Chief complaint:")).toBeLessThan(at("Stand-in draft for subjective.chief_complaint."))
    expect(at("Stand-in draft for subjective.chief_complaint.")).toBeLessThan(at("Diagnoses:"))
    expect(at("Diagnoses:")).toBeLessThan(at("Stand-in diagnosis for assessment.diagnoses"))
    expect(at("F00.0")).toBeGreaterThan(at("Stand-in diagnosis for assessment.diagnoses"))
    expect(at("Mental status exam")).toBeLessThan(at("Medical decision making"))

    // The visit's times and the confirmed minutes, above the note.
    expect(at(`Psychotherapy time: ${windowText}`)).toBeLessThan(at("Chief complaint:"))
    expect(at("Psychotherapy duration: 37 min")).toBeLessThan(at("Chief complaint:"))
    expect(at(visitLine.split(" · ")[0])).toBeLessThan(at("Chief complaint:"))

    // The signature block, after the note.
    expect(at(`Electronically signed by ${SIGNER}`)).toBeGreaterThan(at("Psychotherapy time:"))
    expect(at(`Electronically signed by ${SIGNER}`)).toBeGreaterThan(
      at("Stand-in draft for psychotherapy.therapy_cadence."),
    )
  } finally {
    await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
})
