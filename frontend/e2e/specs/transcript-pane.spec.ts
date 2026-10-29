// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The transcript pane shows the transcript, not how it was stored. A call
 * recorded by the desktop app is stored in the "google_meet" format whatever
 * the video platform was, so the pane must not name a format at all.
 */

import { expect, test } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

test("the transcript pane names no storage format", async ({ signedInPage: page, api }) => {
  const patient = await givePatient(api)
  const session = await api.post<{ id: string }>(`/api/patients/${patient.id}/sessions/upload`, {
    patient_id: patient.id,
    session_date: new Date(Date.now() - 48 * 3_600_000).toISOString(),
    transcript: {
      format: "google_meet",
      content: "[00:00:05] Therapist: How was the week?\n[00:00:09] Client: Better than the last one.",
    },
  })

  await page.goto(`/dashboard/sessions/${session.id}`)

  const pane = page.getByTestId("transcript-pane")
  await expect(pane.getByText("How was the week?")).toBeVisible()
  await expect(pane.getByText(/Format:/)).toHaveCount(0)
  await expect(pane.getByText(/GOOGLE_MEET/i)).toHaveCount(0)
})
