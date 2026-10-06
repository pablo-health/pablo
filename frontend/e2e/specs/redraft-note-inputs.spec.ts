// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A note type's details (its inputs) are supplied on the appointment; after
 * the visit the clinician changes one on the session's note and redrafts it.
 * Edits survive a redraft unless the clinician says otherwise, and a signed
 * note's details can no longer be changed.
 *
 * The stack's drafting stand-in (scripts/fake_llm.py) drafts a field named
 * after an input as that input's value, so the note shows which value
 * reached the draft. Every other field reads "Stand-in draft for …".
 */

import { randomBytes } from "node:crypto"
import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { givePatient } from "../fixtures/scenarios"

const VISIT_TYPE = {
  label: "Visit with a code",
  sections: [
    {
      key: "visit",
      label: "Visit",
      fields: [
        { key: "summary", label: "Summary" },
        { key: "visit_code", label: "Visit code" },
      ],
    },
  ],
  inputs: [
    {
      key: "visit_code",
      label: "Visit code",
      kind: "choice",
      options: ["99213", "99214"],
      required: true,
    },
  ],
}

type Session = { status: string; note: { id: string; status: string } | null }

/** A recorded session of `noteType` with its draft written, booked with `visitCode`. */
async function draftedSession(api: ApiClient, noteType: string, visitCode: string) {
  const patient = await givePatient(api)
  const { id } = await api.post<{ id: string }>("/api/sessions/schedule", {
    patient_id: patient.id,
    scheduled_at: new Date().toISOString(),
    source: "companion",
    note_type: noteType,
    note_inputs: { visit_code: visitCode },
  })
  await api.patch(`/api/sessions/${id}/status`, { status: "in_progress" })
  await api.patch(`/api/sessions/${id}/status`, { status: "recording_complete" })
  await api.post(`/api/sessions/${id}/transcript`, {
    format: "txt",
    content: "[00:00:05] Therapist: How was the week?\n[00:00:09] Client: Steadier.",
  })
  await expect
    .poll(async () => (await api.get<Session>(`/api/sessions/${id}`)).status, { timeout: 30_000 })
    .toBe("pending_review")
  return id
}

test("change a note's details after the visit and redraft it, keeping edits", async ({
  signedInPage: page,
  api,
}) => {
  const slug = `e2e_visit_${randomBytes(3).toString("hex")}`
  const { key } = await api.put<{ key: string }>(`/api/note-types/custom/${slug}`, VISIT_TYPE)
  try {
    const sessionId = await draftedSession(api, key, "99213")
    await page.goto(`/dashboard/sessions/${sessionId}`)

    const body = page.getByTestId("session-note")
    const details = page.getByRole("region", { name: "Note details" })
    await expect(body.getByText("99213", { exact: true })).toBeVisible()
    await expect(details.getByLabel(/Visit code/)).toHaveValue("99213")

    // Change the code and redraft: the draft now carries the new value.
    await details.getByLabel(/Visit code/).selectOption("99214")
    await details.getByRole("button", { name: "Save and redraft" }).click()
    await expect(body.getByText("99214", { exact: true })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText("Redrafting the note…")).toHaveCount(0)

    // Edit the summary, then change the code back: the redraft asks first,
    // and keeping the edits (the default) keeps the summary as written.
    const { note } = await api.get<Session>(`/api/sessions/${sessionId}`)
    const mine = `My own summary ${slug}`
    await api.patch(`/api/notes/${note?.id}`, {
      content_edited: { visit: { summary: mine, visit_code: "99214" } },
    })
    await page.reload()
    await expect(body.getByText(mine)).toBeVisible()
    await details.getByLabel(/Visit code/).selectOption("99213")
    await details.getByRole("button", { name: "Save and redraft" }).click()
    const choice = page.getByRole("dialog", { name: "You've edited this note" })
    await expect(choice.getByRole("radio", { name: /Keep my edits/ })).toBeChecked()
    await choice.getByRole("button", { name: "Redraft" }).click()
    await expect(body.getByText("99213", { exact: true })).toBeVisible({ timeout: 30_000 })
    await expect(body.getByText(mine)).toBeVisible()

    // Signed, the details are read-only.
    await api.post(`/api/notes/${note?.id}/sign`, { signer_name: "Sam Ortiz" })
    await page.reload()
    await expect(details.getByText("99213")).toBeVisible()
    await expect(details.getByRole("combobox")).toHaveCount(0)
    await expect(details.getByRole("button", { name: "Save and redraft" })).toHaveCount(0)
  } finally {
    await api.delete(`/api/note-types/custom/${slug}`)
  }
})
