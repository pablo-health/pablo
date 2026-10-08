// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A transcript uploaded from "New note" as a psychiatric follow-up: the note
 * is drafted as that type, written against the chart, and offers chart
 * updates at sign — the same as a recorded visit of that type.
 *
 * The stack drafts through its stand-in (NOTE_GENERATION_BASE_URL), which
 * writes the chart it was handed into a chart-fed field, and drafts a
 * diagnosis the chart's problem list lacks. So a draft that prints the
 * chart's allergy proves the chart reached the model, and an "Update the
 * chart" step offering that diagnosis proves the proposal pass ran.
 *
 * Catches the type being lost between the dialog and the drafting worker
 * (a SOAP note, with no allergies field and no follow-up sections), and an
 * upload of a typed note skipping the chart or the proposals.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Session = {
  id: string
  status: string
  note: { note_type: string; note_inputs: Record<string, string> | null } | null
}

const FOLLOW_UP_LABEL = "Psychiatric follow-up (E/M + psychotherapy)"

test("a transcript uploaded as a psychiatric follow-up is drafted as one, against the chart", async ({
  api,
  signedInPage: page,
}) => {
  const patient = await givePatient(api)
  await api.put(`/api/patients/${patient.id}/allergies`, {
    status: "recorded",
    allergies: [{ substance: "Penicillin", reaction: "Hives" }],
  })

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByLabel("Notes").getByRole("button", { name: "New note" }).click()
  await page.getByRole("dialog").getByRole("button", { name: /From a transcript/ }).click()

  const dialog = page.getByRole("dialog", { name: "Upload Session Transcript" })
  await expect(dialog.getByRole("combobox", { name: "Note type" })).toHaveText("SOAP")
  await dialog.getByRole("combobox", { name: "Note type" }).click()
  await page.getByRole("option", { name: FOLLOW_UP_LABEL }).click()
  await dialog.getByLabel(/Session Date & Time/).fill("2026-10-06T14:00")
  await dialog.locator("#transcript_file").setInputFiles({
    name: "follow-up.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(
      [
        "[00:00:05] Therapist: How has the new dose been?",
        "[00:00:09] Client: Steadier, and sleeping through most nights.",
      ].join("\n"),
    ),
  })

  // The place of service is asked for before anything is sent.
  await dialog.getByRole("button", { name: "Upload & Draft Note" }).click()
  await expect(dialog.getByText("Place of service is required")).toBeVisible()
  await dialog.getByRole("combobox", { name: "Place of service" }).click()
  await page.getByRole("option", { name: "Telehealth" }).click()

  const uploaded = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/patients/${patient.id}/sessions/upload`) &&
      response.request().method() === "POST",
  )
  await dialog.getByRole("button", { name: "Upload & Draft Note" }).click()
  const response = await uploaded
  expect(response.status(), await response.text()).toBe(202)
  const { id } = (await response.json()) as Session

  await expect
    .poll(async () => (await api.get<Session>(`/api/sessions/${id}`)).status)
    .toBe("pending_review")
  const drafted = (await api.get<Session>(`/api/sessions/${id}`)).note
  expect(drafted?.note_type).toBe("psychiatric_follow_up")
  expect(drafted?.note_inputs).toMatchObject({ place_of_service: "Telehealth" })

  await page.goto(`/dashboard/sessions/${id}`)
  const note = page.getByTestId("session-note")
  await expect(note.getByRole("heading", { name: FOLLOW_UP_LABEL })).toBeVisible()
  await expect(note.getByText("Penicillin (Hives)", { exact: true })).toBeVisible()

  // Signing offers the chart update the visit stated, as a recorded visit does.
  await page.getByRole("button", { name: "Sign and lock" }).click()
  const signing = page.getByRole("dialog")
  await expect(signing.getByRole("heading", { name: "Update the chart" })).toBeVisible()
  await expect(signing.getByRole("button", { name: /to problem list$/ })).toBeVisible()
})
