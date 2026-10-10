// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A detached copy of a built-in keeps printing its chart-fed fields from the
 * chart: start from the psychiatric follow-up, detach it into a full type of
 * the practice's own, save it, and draft a visit for a client whose chart
 * records an allergy. The Allergies field prints the chart's record.
 *
 * The stack drafts through its stand-in (NOTE_GENERATION_BASE_URL), which
 * answers every field it is asked for with "Stand-in draft for
 * <section>.<field>." and says nothing about a chart-fed field unless a
 * client line reads "Update on <key>: <text>". So the chart's text in the
 * note, and no stand-in text for that field, means code wrote it.
 */

import { randomBytes } from "node:crypto"

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

const BASE_LABEL = "Psychiatric follow-up (E/M + psychotherapy)"

type Saved = { key: string; based_on: unknown }
type Session = { id: string; status: string }
type Stored = { spec: { sections: { key: string; fields: { key: string; source?: string }[] }[] } }

test("a detached copy of the follow-up prints the chart's allergy as recorded", async ({
  api,
  signedInPage: page,
}) => {
  const name = `Detached follow-up ${randomBytes(3).toString("hex")}`
  let slug: string | null = null
  try {
    await page.goto("/dashboard/settings/note-types")
    await page.getByRole("button", { name: `Start from ${BASE_LABEL}` }).click()
    await page.getByLabel("Note type name").fill(name)
    await page.getByRole("button", { name: `Detach from ${BASE_LABEL}` }).click()
    await page.getByRole("group", { name: "Detach" }).getByRole("button", { name: "Detach" }).click()

    // Every part is editable now; a printed field says where it comes from instead of offering a hint.
    const field = (label: string) =>
      page.getByRole("group", { name: /^Field \d+$/ }).filter({ has: page.locator(`input[value="${label}"]`) })
    await expect(field("Allergies").getByText("From the chart", { exact: true })).toBeVisible()
    await expect(field("Allergies").getByLabel("What goes here")).toHaveCount(0)
    await expect(field("Place of service").getByText("From the visit", { exact: true })).toBeVisible()
    await expect(field("Place of service").getByText("From the chart", { exact: true })).toHaveCount(0)

    const saved = page.waitForResponse(
      (r) => new URL(r.url()).pathname.startsWith("/api/note-types/custom/") && r.request().method() === "PUT",
    )
    await page.getByRole("button", { name: "Save note type" }).click()
    const savedResponse = await saved
    expect(savedResponse.status(), await savedResponse.text()).toBe(200)
    const savedType = (await savedResponse.json()) as Saved
    slug = savedType.key.replace(/^custom\./, "")
    expect(savedType.based_on).toBeNull()

    const stored = await api.get<Stored>(`/api/note-types/${savedType.key}`)
    const medications = stored.spec.sections.find((s) => s.key === "medications")
    expect(medications?.fields.find((f) => f.key === "allergies")?.source).toBe("allergies")

    const patient = await givePatient(api)
    await api.put(`/api/patients/${patient.id}/allergies`, {
      status: "recorded",
      allergies: [{ substance: "Sulfa", reaction: "Rash" }],
    })
    const session = await api.post<Session>("/api/sessions/schedule", {
      patient_id: patient.id,
      scheduled_at: new Date().toISOString(),
      source: "companion",
      video_platform: "zoom",
      note_type: savedType.key,
      note_inputs: { place_of_service: "In office" },
    })
    await api.patch(`/api/sessions/${session.id}/status`, { status: "in_progress" })
    await api.patch(`/api/sessions/${session.id}/status`, { status: "recording_complete" })
    await api.post(`/api/sessions/${session.id}/transcript`, {
      format: "txt",
      content:
        "[00:00:05] Therapist: How has sleep been since the last visit?\n" +
        "[00:00:09] Client: A little better, most nights.",
    })
    await expect
      .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status, {
        timeout: 30_000,
      })
      .toBe("pending_review")

    await page.goto(`/dashboard/sessions/${session.id}`)
    await expect(page.getByText("Sulfa (Rash)", { exact: true })).toBeVisible()
    await expect(page.getByText("Stand-in draft for medications.allergies.")).toHaveCount(0)
    // The fields the copy still drafts go to the model as before.
    await expect(page.getByText("Stand-in draft for subjective.chief_complaint.")).toBeVisible()
  } finally {
    if (slug) await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
})
