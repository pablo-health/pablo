// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Visit times on a recorded visit's note, and the psychotherapy minutes the
 * clinician confirms.
 *
 * The stack drafts through its stand-in (scripts/fake_llm.py): every field
 * reads "Stand-in draft for <section>.<field>.", and the stand-in labels each
 * turn by its words: a medication or screening question as such, the rest
 * therapy from the clinician's spoken cue ("let's get into"). It hears
 * "Psychotherapy N minutes" in the addendum as dictated minutes.
 */

import { randomBytes } from "node:crypto"

import type { ApiClient } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Session = { id: string; status: string }
type Dictations = { data: Array<{ status: string }> }
type PsychotherapyContent = { psychotherapy?: { psychotherapy_time?: string } }
type NoteOnSession = {
  note: { content: PsychotherapyContent | null; content_edited: PsychotherapyContent | null }
}

// A clip dictated afterwards: Chromium's fake microphone (a tone).
test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  },
})

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

// Therapy from the clinician's cue at 1:00, a medication check from 15:00 to
// 20:00, therapy again to 40:00, a risk question, and the client leaving at
// 45:00 (six words: client time ends at 45:02), then a dictated addendum.
// The therapy is 14 + 20 = 34 minutes, interleaved; read as therapy too, the
// medication check makes one run of 39.
const TURNS = [
  "[00:00:05] Therapist: Hi, good to see you today.",
  "[00:01:00] Therapist: Now let's get into the session work you wanted.",
  "[00:01:30] Client: I keep replaying the argument with my sister every night.",
  "[00:15:00] Therapist: Quick check: any side effects since the dose change?",
  "[00:16:00] Client: No, sleep is better than it was before.",
  "[00:20:00] Therapist: Back to the argument. What did you tell yourself afterwards?",
  "[00:40:00] Therapist: Any thoughts of hurting yourself?",
  "[00:40:30] Client: No, none at all, not even close.",
  "[00:45:00] Client: Thank you, see you next month then.",
]
const VISIT = [...TURNS, "[00:46:00] Therapist: Addendum for the note. Client denies suicidal ideation."].join(
  "\n",
)
const VISIT_WITH_MINUTES = [...TURNS, "[00:46:00] Therapist: Addendum for the note. Psychotherapy 30 minutes."].join(
  "\n",
)

async function recordedVisit(api: ApiClient, noteType: string, content = VISIT): Promise<Session> {
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
  await api.post(`/api/sessions/${session.id}/transcript`, { format: "txt", content })
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
  test("the clinician confirms interleaved therapy, relabels a run, and the note states the time", async ({
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

      // Every turn while the client was present is labeled; the therapy adds up.
      const runs = times.getByTestId("timeline-run")
      await expect(runs).toHaveCount(6)
      await expect(runs.nth(2)).toHaveAttribute("data-label", "medication_management")
      const preview = times.getByTestId("psychotherapy-preview")
      await expect(preview).toHaveText("34 therapy minutes of 45 · 16–37 minutes")
      await expect(times.getByTestId("em-remainder")).toHaveText("Medical visit: 11 min")

      // Confirmed as labeled: the minutes, and that they interleaved.
      await times.getByRole("button", { name: "Confirm" }).click()
      const confirmed = times.getByTestId("psychotherapy-confirmed")
      await expect(confirmed).toHaveText(
        "34 minutes (interleaved with medication management; time accounted separately)",
      )
      await expect(times.getByTestId("add-on-band")).toHaveText("16–37 minutes")
      await expect(times.getByTestId("durations-line")).toHaveText(
        /^Total duration: \d+ min · Psychotherapy duration: 34 min$/,
      )

      // The medication check was therapy after all: one run, a window, the next band.
      await times.getByRole("button", { name: "Change" }).click()
      await runs.nth(2).click()
      await times.getByRole("group", { name: /^Label / }).getByRole("button", { name: "Therapy" }).click()
      await expect(runs).toHaveCount(4)
      await expect(preview).toHaveText("39 therapy minutes of 45 · 38–52 minutes")
      await expect(times.getByTestId("em-remainder")).toHaveText("Medical visit: 6 min")
      await times.getByRole("button", { name: "Confirm" }).click()
      await expect(confirmed).toHaveText(/^\d{1,2}:\d{2} [AP]M to \d{1,2}:\d{2} [AP]M, 39 minutes$/)
      await expect(times.getByTestId("add-on-band")).toHaveText("38–52 minutes")
      await expect(times.getByTestId("durations-line")).toContainText("Psychotherapy duration: 39 min")
      // Nothing was dictated, so there is nothing to settle.
      await expect(times.getByRole("alert")).toHaveCount(0)

      const windowText = await confirmed.innerText()
      // Confirming is not an edit: the time goes into the draft.
      const saved = await api.get<NoteOnSession>(`/api/sessions/${session.id}`)
      expect(saved.note.content?.psychotherapy?.psychotherapy_time).toBe(windowText)
      expect(saved.note.content_edited).toBeNull()
      // On the note itself as well as in the panel above it.
      await expect(page.getByText(windowText, { exact: true })).toHaveCount(2)

      // So dictating more redrafts without asking about edits, and keeps the time.
      const panel = page.getByRole("region", { name: "Dictate more" })
      await panel.getByRole("button", { name: "Dictate more" }).click()
      await expect(panel.getByText(/Recording 0:0[1-9]/)).toBeVisible({ timeout: 10_000 })
      await panel.getByRole("button", { name: "Stop" }).click()
      const sent = page.waitForResponse(
        (r) => /\/api\/sessions\/[\w-]+\/dictations$/.test(r.url()) && r.request().method() === "POST",
      )
      await panel.getByRole("button", { name: "Add to note" }).click()
      // Sent straight away: no "You've edited this note" question in between.
      expect((await sent).status()).toBe(202)
      await expect(page.getByRole("dialog", { name: "You've edited this note" })).toHaveCount(0)
      await expect
        .poll(
          async () =>
            (await api.get<{ note: { status: string } }>(`/api/sessions/${session.id}`)).note
              .status,
          { timeout: 30_000 },
        )
        .toBe("complete")
      await page.reload()
      await expect(page.getByText(windowText, { exact: true })).toHaveCount(2)
    })
  })

  test("minutes the clinician dictated win over the labeled turns", async ({
    api,
    signedInPage: page,
  }) => {
    await withNoteType(api, withPsychotherapy, async (key) => {
      const session = await recordedVisit(api, key, VISIT_WITH_MINUTES)
      await page.goto(`/dashboard/sessions/${session.id}`)

      const times = page.getByTestId("visit-times")
      await expect(times.getByTestId("psychotherapy-preview")).toHaveText(
        "34 therapy minutes of 45 · 16–37 minutes",
      )
      await expect(times.getByText("You said 30 minutes.")).toBeVisible()
      await times.getByRole("button", { name: "Use my minutes" }).click()

      await expect(times.getByTestId("psychotherapy-confirmed")).toHaveText("30 minutes")
      await expect(times.getByTestId("add-on-band")).toHaveText("16–37 minutes")
      await expect(times.getByRole("alert")).toHaveCount(0)
      const saved = await api.get<NoteOnSession>(`/api/sessions/${session.id}`)
      expect(saved.note.content?.psychotherapy?.psychotherapy_time).toBe("30 minutes")
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
