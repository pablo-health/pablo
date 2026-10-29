// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A recorded call where the clinician keeps recording after the client has
 * gone: the note shows when the client left and how long the clinician
 * dictated afterwards, measured from the transcript's two speakers. A
 * recording with no client on it reads as a dictation.
 *
 * The stack drafts through its stand-in (scripts/fake_llm.py), so this checks
 * the times, not the drafted wording.
 */

import type { ApiClient } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Session = {
  id: string
  status: string
  client_present_end_seconds: number | null
  clinician_addendum_seconds: number | null
}

// The client's last line starts at 35:56 and runs six words; the clinician
// then dictates for about three minutes, and a stray "Okay." on the client's
// channel at the end is too short to count as the client coming back.
const CALL_WITH_TAIL = [
  "[00:00:05] Therapist: Good to see you. How has the week been?",
  "[00:10:00] Client: Better, the new dose helps me sleep through the night.",
  "[00:35:56] Client: Thank you, see you next month.",
  "[00:36:30] Therapist: Addendum for the note. Client denies suicidal ideation, intent or plan.",
  "[00:39:00] Therapist: Plan continues as discussed today.",
  "[00:39:20] Client: Okay.",
].join("\n")

const DICTATION = [
  "[00:00:02] Therapist: Dictating for the visit earlier today. No risk concerns were stated.",
  "[00:01:10] Therapist: Continue the current medication and follow up in four weeks.",
].join("\n")

/** A video call, recorded, with `transcript` handed over the way a recording's arrives. */
async function recordedCall(api: ApiClient, transcript: string): Promise<Session> {
  const patient = await givePatient(api)
  const scheduled = await api.post<Session>("/api/sessions/schedule", {
    patient_id: patient.id,
    scheduled_at: new Date().toISOString(),
    source: "companion",
    video_platform: "zoom",
  })
  await api.patch(`/api/sessions/${scheduled.id}/status`, { status: "in_progress" })
  await api.patch(`/api/sessions/${scheduled.id}/status`, { status: "recording_complete" })
  await api.post(`/api/sessions/${scheduled.id}/transcript`, { format: "txt", content: transcript })
  await expect
    .poll(async () => (await api.get<Session>(`/api/sessions/${scheduled.id}`)).status, {
      timeout: 30_000,
    })
    .toBe("pending_review")
  return api.get<Session>(`/api/sessions/${scheduled.id}`)
}

test.describe("client present on the recording", () => {
  test("the note shows when the client left and the dictated addendum after", async ({
    api,
    signedInPage: page,
  }) => {
    const session = await recordedCall(api, CALL_WITH_TAIL)

    // 35:56 plus six words at 2.5 words a second: client time ends at 35:58.
    expect(session.client_present_end_seconds).toBeCloseTo(2158.4, 1)
    expect(Math.floor((session.clinician_addendum_seconds ?? 0) / 60)).toBe(3)

    await page.goto(`/dashboard/sessions/${session.id}`)
    await expect(page.getByTestId("client-present-line")).toHaveText(
      /^Client present until \d{1,2}:\d{2} [AP]M · Your dictated addendum: 3 min$/,
    )
  })

  test("a call with no client on it reads as a dictation", async ({ api, signedInPage: page }) => {
    const session = await recordedCall(api, DICTATION)

    expect(session.client_present_end_seconds).toBe(0)

    await page.goto(`/dashboard/sessions/${session.id}`)
    await expect(page.getByTestId("client-present-line")).toHaveText("Dictation only, 1 min")
  })
})
