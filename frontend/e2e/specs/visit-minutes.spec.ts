// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Visit times on a recorded visit's note, and the psychotherapy minutes the
 * clinician confirms.
 *
 * The stack drafts through its stand-in (scripts/fake_llm.py): every field
 * reads "Stand-in draft for <section>.<field>.", and the stand-in marks the
 * therapy portion's start at the clinician's spoken cue ("let's get into").
 * Its psychotherapy time field is therefore a stated value that disagrees
 * with the confirmed window, which is exactly the conflict the clinician
 * settles here.
 */

import { randomBytes } from "node:crypto"

import type { ApiClient } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Session = { id: string; status: string }
type Dictations = { data: Array<{ status: string }> }
type NoteOnSession = {
  note: { content_edited: { psychotherapy?: { psychotherapy_time?: string } } | null }
}

const withPsychotherapy = {
  label: "Follow-up with therapy (e2e)",
  sections: [
    { key: "plan", label: "Plan", fields: [{ key: "follow_up", label: "Follow-up" }] },
    {
      key: "psychotherapy",
      label: "Psychotherapy",
      fields: [
        { key: "psychotherapy_time", label: "Psychotherapy time" },
        { key: "modality_interventions", label: "Modality and interventions" },
      ],
    },
  ],
}

const withoutPsychotherapy = {
  label: "Medication check (e2e)",
  sections: [{ key: "plan", label: "Plan", fields: [{ key: "follow_up", label: "Follow-up" }] }],
}

// A twelve-minute medication check, the clinician's cue at 12:30, the client
// leaving at 50:00 (six words: client time ends at 50:02), then a dictated
// addendum. 12:30 to 50:02 is 37 whole minutes: the top of the 16-37 band.
const VISIT = [
  "[00:00:05] Therapist: How has the medication been since the dose change?",
  "[00:03:00] Client: Better sleep, and no side effects I have noticed.",
  "[00:11:50] Therapist: Good, we will keep the dose where it is for now.",
  "[00:12:30] Therapist: Now let's get into the session work you wanted.",
  "[00:30:00] Client: I keep replaying the argument with my sister every night.",
  "[00:50:00] Client: Thank you, see you next month.",
  "[00:51:00] Therapist: Addendum for the note. Client denies suicidal ideation.",
].join("\n")

async function recordedVisit(api: ApiClient, noteType: string): Promise<Session> {
  const patient = await givePatient(api)
  const session = await api.post<Session>("/api/sessions/schedule", {
    patient_id: patient.id,
    scheduled_at: new Date().toISOString(),
    source: "companion",
    video_platform: "zoom",
    note_type: noteType,
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

async function withNoteType<T>(
  api: ApiClient,
  spec: object,
  body: (key: string) => Promise<T>,
): Promise<T> {
  const slug = `e2e_minutes_${randomBytes(3).toString("hex")}`
  await api.request("PUT", `/api/note-types/custom/${slug}`, spec)
  try {
    return await body(`custom.${slug}`)
  } finally {
    await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
}

test.describe("visit minutes", () => {
  test("the clinician confirms the therapy start and the note states the window", async ({
    api,
    signedInPage: page,
  }) => {
    await withNoteType(api, withPsychotherapy, async (key) => {
      const session = await recordedVisit(api, key)
      await page.goto(`/dashboard/sessions/${session.id}`)

      const times = page.getByTestId("visit-times")
      await expect(times.getByTestId("visit-line")).toHaveText(
        /^Started \d{1,2}:\d{2} [AP]M · Ended \d{1,2}:\d{2} [AP]M · \d+ min$/,
      )
      await expect(times.getByTestId("client-present-line")).toContainText("Client present until")
      // No E/M time beside a psychotherapy add-on.
      await expect(times.getByTestId("documentation-total")).toHaveCount(0)

      // The clinician's cue is proposed, and minutes run to when the client left.
      await expect(page.getByRole("radio", { name: /\(you said so here\)/ })).toBeChecked()
      await expect(page.getByTestId("psychotherapy-preview")).toHaveText(
        "37 minutes · 16–37 minutes",
      )

      // Typing more minutes than the client was present is refused.
      await page.getByRole("radio", { name: "Type the minutes" }).check()
      await page.getByLabel("Psychotherapy minutes").fill("51")
      await expect(times.getByRole("alert")).toContainText("the 50 minutes the client was present")
      await expect(page.getByRole("button", { name: "Confirm" })).toBeDisabled()

      await page.getByRole("radio", { name: /\(you said so here\)/ }).check()
      await page.getByRole("button", { name: "Confirm" }).click()
      await expect(page.getByTestId("psychotherapy-confirmed")).toHaveText(
        /^\d{1,2}:\d{2} [AP]M to \d{1,2}:\d{2} [AP]M, 37 minutes$/,
      )
      await expect(page.getByTestId("add-on-band")).toHaveText("16–37 minutes")

      // The draft already holds a stated time; the clinician picks the window.
      const conflict = times.getByRole("alert")
      await expect(conflict).toContainText("You said “Stand-in draft for psychotherapy.psychotherapy_time.”")
      await conflict.getByRole("button", { name: "Use 37 minutes" }).click()
      await expect(conflict).toHaveCount(0)
      const windowText = await page.getByTestId("psychotherapy-confirmed").innerText()
      const saved = await api.get<NoteOnSession>(`/api/sessions/${session.id}`)
      expect(saved.note.content_edited?.psychotherapy?.psychotherapy_time).toBe(windowText)
      // On the note itself as well as in the panel above it.
      await expect(page.getByText(windowText, { exact: true })).toHaveCount(2)
    })
  })

  test("a note without psychotherapy shows total time with documentation", async ({
    api,
    signedInPage: page,
  }) => {
    await withNoteType(api, withoutPsychotherapy, async (key) => {
      const session = await recordedVisit(api, key)
      await page.goto(`/dashboard/sessions/${session.id}`)

      const total = page.getByTestId("documentation-total")
      await expect(total).toHaveText(/^Total time on this date, including documentation: \d+ min$/)
      await expect(page.getByTestId("psychotherapy-window")).toHaveCount(0)
      const before = Number((await total.innerText()).match(/(\d+) min$/)?.[1])

      // Three minutes dictated for the note afterwards count toward the total.
      const form = new FormData()
      form.append("audio", new Blob([new Uint8Array(3200)], { type: "application/octet-stream" }), "clip.pcm")
      form.append("duration_seconds", "180")
      await api.postForm(`/api/sessions/${session.id}/dictations`, form)
      await expect
        .poll(
          async () =>
            (await api.get<Dictations>(`/api/sessions/${session.id}/dictations`)).data[0]?.status,
          { timeout: 30_000 },
        )
        .toBe("transcribed")

      await page.reload()
      await expect(total).toHaveText(
        `Total time on this date, including documentation: ${before + 3} min`,
      )
    })
  })
})
