// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The psychiatric initial evaluation template, end to end: start from it in
 * Settings > Note types, try a draft on its sample visit, save it, then draft
 * a session with it, read the stated diagnoses on the note, and add one to
 * the client's problem list.
 *
 * The stack drafts through its stand-in (NOTE_GENERATION_BASE_URL): every
 * text field reads "Stand-in draft for <section>.<field>.", and a diagnoses
 * field holds one diagnosis, "Stand-in diagnosis for <section>.<field>",
 * with the code F00.0 — so the note shows the code kept beside its diagnosis.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

const TEMPLATE_LABEL = "Psychiatric initial evaluation"
const STATED_LABEL = "Stand-in diagnosis for assessment.diagnoses"
const STATED_DIAGNOSIS = `${STATED_LABEL} (F00.0)`

type Session = {
  id: string
  status: string
  note: { id: string; note_type: string; content: unknown } | null
}
type Problem = { label: string; icd10_code: string | null; status: string; source_note_id: string | null }

test("a practice starts the initial evaluation from its template and its notes keep diagnoses as stated", async ({
  signedInPage: page,
  api,
}) => {
  let slug: string | null = null
  try {
    await page.goto("/dashboard/settings/note-types")
    await page.getByRole("button", { name: `Start from ${TEMPLATE_LABEL}` }).click()
    await expect(page.getByLabel("Note type name")).toHaveValue(TEMPLATE_LABEL)

    // Try it on the template's sample visit. Place of service is required.
    await expect(page.getByRole("radio", { name: "Sample visit" })).toHaveAttribute("aria-checked", "true")
    await page.getByLabel(/^Place of service/).selectOption("Telehealth")
    await page.getByLabel(/^Visit code/).selectOption("Psychiatric diagnostic evaluation (90792)")
    const previewed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/note-types/preview" && r.request().method() === "POST",
    )
    await page.getByRole("button", { name: "Draft a note" }).click()
    expect((await previewed).status()).toBe(200)
    const draft = page.getByTestId("try-it-draft")
    await expect(draft.getByText("Stand-in draft for hpi.onset_course.")).toBeVisible()
    await expect(draft.getByText("Stand-in draft for psychiatric_ros.mania_hypomania.")).toBeVisible()
    await expect(draft.getByText(STATED_DIAGNOSIS)).toBeVisible()

    const saved = page.waitForResponse(
      (r) => new URL(r.url()).pathname.startsWith("/api/note-types/custom/") && r.request().method() === "PUT",
    )
    await page.getByRole("button", { name: "Save note type" }).click()
    const savedResponse = await saved
    expect(savedResponse.status(), await savedResponse.text()).toBe(200)
    const savedType = (await savedResponse.json()) as { key: string; version: number }
    slug = savedType.key.replace(/^custom\./, "")
    await expect(page.getByRole("status")).toHaveText(`Saved ${TEMPLATE_LABEL}, version ${savedType.version}.`)

    // A first visit drafted with it: scheduled with the type and its inputs,
    // recorded, and its transcript handed over the way a recording's arrives.
    const patient = await givePatient(api)
    const session = await api.post<Session>("/api/sessions/schedule", {
      patient_id: patient.id,
      scheduled_at: new Date().toISOString(),
      source: "companion",
      note_type: savedType.key,
      note_inputs: { place_of_service: "Telehealth", visit_code: "Psychiatric diagnostic evaluation (90792)" },
    })
    await api.patch(`/api/sessions/${session.id}/status`, { status: "in_progress" })
    await api.patch(`/api/sessions/${session.id}/status`, { status: "recording_complete" })
    await api.post(`/api/sessions/${session.id}/transcript`, {
      format: "txt",
      content: "[00:00:05] Therapist: What brings you in?\n[00:00:09] Client: I've been down for months.",
    })
    await expect
      .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status, { timeout: 30_000 })
      .toBe("pending_review")

    await page.goto(`/dashboard/sessions/${session.id}`)
    await expect(page.getByRole("heading", { name: "Assessment and diagnoses" })).toBeVisible()
    const stated = page.getByRole("listitem").filter({ hasText: STATED_DIAGNOSIS })
    await expect(stated).toBeVisible()

    // Carry it onto the chart's problem list, as stated: its code, and the
    // note it came from.
    const added = page.waitForResponse(
      (r) => new URL(r.url()).pathname === `/api/patients/${patient.id}/problems` && r.request().method() === "POST",
    )
    await stated.getByRole("button", { name: `Add ${STATED_LABEL} to problem list` }).click()
    expect((await added).status()).toBe(201)
    await expect(stated.getByText("On problem list")).toBeVisible()

    const noteId = (await api.get<Session>(`/api/sessions/${session.id}`)).note?.id
    const problems = await api.get<{ data: Problem[] }>(`/api/patients/${patient.id}/problems`)
    expect(problems.data).toEqual([
      expect.objectContaining({ label: STATED_LABEL, icd10_code: "F00.0", status: "active", source_note_id: noteId }),
    ])

    // The chart shows it, and the note still offers nothing more to add.
    await page.goto(`/dashboard/patients/${patient.id}?tab=problems`)
    await expect(page.getByTestId("problem-row").filter({ hasText: STATED_LABEL })).toBeVisible()
    await page.goto(`/dashboard/sessions/${session.id}`)
    await expect(stated.getByText("On problem list")).toBeVisible()
    await expect(stated.getByRole("button", { name: /to problem list/ })).toHaveCount(0)
  } finally {
    if (slug) await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
})
