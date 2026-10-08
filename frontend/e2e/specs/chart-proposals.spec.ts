// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Chart updates at sign, end to end: a visit that changes the chart proposes
 * the update, the clinician accepts one and discards another while signing,
 * and the chart changes only where accepted. An intake proposes its own
 * history; accepting it fills the chart, and the next follow-up prints it.
 *
 * The stack drafts through its stand-in (NOTE_GENERATION_BASE_URL). Its
 * proposal call answers a client line "Update on <key>: <text>" with a
 * proposal for that field citing that line, and its drafts fill a field
 * whose key is a chart-history key with the chart's text.
 */

import { randomBytes } from "node:crypto"

import type { Page } from "@playwright/test"

import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { givePatient } from "../fixtures/scenarios"

type Session = { id: string; status: string }
type Appointment = { id: string }
type HistoryField = { key: string; text: string | null; source_note_id: string | null }
type ChartHistory = { groups: { fields: HistoryField[] }[] }

const FROM_THE_CHART = "From the chart; write it exactly as given, or 'Not recorded'"

async function historyText(api: ApiClient, patientId: string, key: string) {
  const chart = await api.get<ChartHistory>(`/api/patients/${patientId}/chart-history`)
  return chart.groups.flatMap((g) => g.fields).find((f) => f.key === key)
}

/** A visit of the given type with the given transcript, drafted and waiting for review. */
async function draftedVisit(
  api: ApiClient,
  patientId: string,
  noteType: string,
  transcript: string,
  cleanup: string[],
): Promise<Session> {
  const startAt = new Date(Date.now() - (30 + Math.floor(Math.random() * 500)) * 60 * 60 * 1000)
  const appointment = await api.post<Appointment>("/api/appointments", {
    patient_id: patientId,
    title: "Visit",
    start_at: startAt.toISOString(),
    end_at: new Date(startAt.getTime() + 30 * 60 * 1000).toISOString(),
    duration_minutes: 30,
    session_type: "individual",
    note_type: noteType,
  })
  cleanup.push(appointment.id)
  const session = await api.post<Session>(`/api/appointments/${appointment.id}/start-session`, {
    recording: true,
  })
  await api.patch(`/api/sessions/${session.id}/status`, { status: "in_progress" })
  await api.patch(`/api/sessions/${session.id}/status`, { status: "recording_complete" })
  await api.post(`/api/sessions/${session.id}/transcript`, { format: "txt", content: transcript })
  await expect
    .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status)
    .toBe("pending_review")
  return session
}

async function openSignStep(page: Page, sessionId: string) {
  await page.goto(`/dashboard/sessions/${sessionId}`)
  await page.getByRole("button", { name: "Sign and lock" }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByRole("heading", { name: "Update the chart" })).toBeVisible()
  return dialog
}

test.describe("chart updates at sign", () => {
  test("an accepted update changes the chart from the note, and a discarded one does not", async ({
    api,
    signedInPage: page,
  }) => {
    const patient = await givePatient(api)
    await api.put(`/api/patients/${patient.id}/chart-history/work_school`, {
      text: "Works full time at the library.",
    })
    await api.put(`/api/patients/${patient.id}/chart-history/supports`, {
      text: "Sister nearby.",
    })
    const slug = `e2e_updates_${randomBytes(3).toString("hex")}`
    await api.put(`/api/note-types/custom/${slug}`, {
      label: `Follow-up ${slug}`,
      sections: [
        {
          key: "social_history",
          label: "Social history",
          fields: [
            { key: "work_school", label: "Work or school", ai_hint: FROM_THE_CHART },
            { key: "supports", label: "Supports", ai_hint: FROM_THE_CHART },
          ],
        },
      ],
    })
    const appointments: string[] = []
    try {
      const session = await draftedVisit(
        api,
        patient.id,
        `custom.${slug}`,
        [
          "[00:00:05] Therapist: Anything new since last time?",
          "[00:00:09] Client: Update on work_school: Worked at the library until March; no longer working there.",
          "[00:00:15] Client: Update on supports: Sister and a neighbor.",
        ].join("\n"),
        appointments,
      )

      const dialog = await openSignStep(page, session.id)
      const work = dialog.getByRole("listitem", { name: "Social history and supports: Work or school" })
      await expect(work).toContainText("Works full time at the library.")
      await expect(work).toContainText("Client: Update on work_school:")
      const supports = dialog.getByRole("listitem", { name: "Social history and supports: Supports" })
      await work.getByRole("button", { name: "Accept" }).click()
      await supports.getByRole("button", { name: "Discard" }).click()
      await expect(dialog.getByRole("heading", { name: "Update the chart" })).toBeHidden()
      await dialog.getByRole("button", { name: "Sign and lock" }).click()
      await expect
        .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status)
        .toBe("finalized")

      const work_school = await historyText(api, patient.id, "work_school")
      expect(work_school?.text).toBe(
        "Worked at the library until March; no longer working there.",
      )
      expect(work_school?.source_note_id).toBeTruthy()
      expect((await historyText(api, patient.id, "supports"))?.text).toBe("Sister nearby.")

      // The signed note keeps what was decided, and nothing is offered twice.
      await page.reload()
      const panel = page.getByTestId("chart-updates")
      await expect(panel.getByText("Added to the chart", { exact: true })).toBeVisible()
      await expect(panel.getByText("Discarded")).toBeVisible()
      await expect(panel.getByRole("button", { name: "Accept" })).toHaveCount(0)
    } finally {
      for (const id of appointments) await api.delete(`/api/appointments/${id}`)
      await api.request("DELETE", `/api/note-types/custom/${slug}`)
    }
  })

  test("an accepted start and stop change the medication list from the note", async ({
    api,
    signedInPage: page,
  }) => {
    const patient = await givePatient(api)
    await api.post(`/api/patients/${patient.id}/medications`, {
      drug_name: "trazodone",
      dose: "50 mg",
      frequency: "at bedtime",
      category: "psychiatric",
    })
    const slug = `e2e_meds_${randomBytes(3).toString("hex")}`
    await api.put(`/api/note-types/custom/${slug}`, {
      label: `Follow-up ${slug}`,
      sections: [
        {
          key: "social_history",
          label: "Social history",
          fields: [{ key: "work_school", label: "Work or school", ai_hint: FROM_THE_CHART }],
        },
      ],
    })
    const appointments: string[] = []
    try {
      const session = await draftedVisit(
        api,
        patient.id,
        `custom.${slug}`,
        [
          "[00:00:05] Clinician: How are the afternoons?",
          "[00:00:09] Clinician: Medication start: hydroxyzine; 25 mg; in the afternoon as needed",
          "[00:00:15] Clinician: Medication stop: trazodone; ; ; nausea",
        ].join("\n"),
        appointments,
      )

      const dialog = await openSignStep(page, session.id)
      const start = dialog.getByRole("listitem", { name: "Medications: Start hydroxyzine" })
      const stop = dialog.getByRole("listitem", { name: "Medications: Stop trazodone" })
      await expect(start).toContainText("Not on the list")
      await expect(start).toContainText("hydroxyzine 25 mg, in the afternoon as needed")
      await expect(stop).toContainText("trazodone 50 mg, at bedtime")
      await expect(stop).toContainText("Stopped: nausea")
      await expect(dialog.getByRole("button", { name: "Edit" })).toHaveCount(0)
      await start.getByRole("button", { name: "Accept" }).click()
      await stop.getByRole("button", { name: "Accept" }).click()
      await expect(dialog.getByRole("heading", { name: "Update the chart" })).toBeHidden()
      await dialog.getByRole("button", { name: "Sign and lock" }).click()
      await expect
        .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status)
        .toBe("finalized")

      await page.goto(`/dashboard/patients/${patient.id}?tab=medications`)
      const started = page.getByRole("listitem").filter({ hasText: "hydroxyzine" })
      await expect(started).toContainText("25 mg, in the afternoon as needed")
      await expect(started).toContainText("Active")
      const stopped = page.getByRole("listitem").filter({ hasText: "trazodone" })
      await expect(stopped).toContainText("Stopped: nausea")
      await expect(stopped).toContainText("Discontinued")
    } finally {
      for (const id of appointments) await api.delete(`/api/appointments/${id}`)
      await api.request("DELETE", `/api/note-types/custom/${slug}`)
    }
  })

  test("signing an intake fills the chart, and the next follow-up prints it", async ({
    api,
    signedInPage: page,
  }) => {
    const patient = await givePatient(api)
    const suffix = randomBytes(3).toString("hex")
    const intake = `e2e_intake_${suffix}`
    const followUp = `e2e_follow_${suffix}`
    await api.put(`/api/note-types/custom/${intake}`, {
      label: `Evaluation ${intake}`,
      sections: [
        {
          key: "social_history",
          label: "Social history",
          fields: [
            { key: "relationships", label: "Relationships", ai_hint: "Partner and family." },
            { key: "work_school", label: "Work or school", ai_hint: "Occupation." },
          ],
        },
      ],
    })
    await api.put(`/api/note-types/custom/${followUp}`, {
      label: `Follow-up ${followUp}`,
      sections: [
        {
          key: "social_history",
          label: "Social history",
          fields: [
            { key: "relationships", label: "Relationships", ai_hint: FROM_THE_CHART },
            { key: "work_school", label: "Work or school", ai_hint: FROM_THE_CHART },
          ],
        },
      ],
    })
    const appointments: string[] = []
    try {
      const first = await draftedVisit(
        api,
        patient.id,
        `custom.${intake}`,
        "[00:00:05] Therapist: Tell me about yourself.",
        appointments,
      )
      const dialog = await openSignStep(page, first.id)
      const rows = dialog.getByTestId("chart-proposal")
      await expect(rows).toHaveCount(2)
      await expect(rows.first()).toContainText("Recorded this visit")
      for (const name of ["Relationships", "Work or school"]) {
        await dialog
          .getByRole("listitem", { name: `Social history and supports: ${name}` })
          .getByRole("button", { name: "Accept" })
          .click()
      }
      await dialog.getByRole("button", { name: "Sign and lock" }).click()
      await expect
        .poll(async () => (await historyText(api, patient.id, "relationships"))?.text)
        .toBe("Stand-in draft for social_history.relationships.")

      const second = await draftedVisit(
        api,
        patient.id,
        `custom.${followUp}`,
        "[00:00:05] Therapist: How have things been?",
        appointments,
      )
      await page.goto(`/dashboard/sessions/${second.id}`)
      await expect(
        page.getByTestId("session-note").getByText("Stand-in draft for social_history.relationships."),
      ).toBeVisible()
      await expect(
        page.getByTestId("session-note").getByText("Stand-in draft for social_history.work_school."),
      ).toBeVisible()
    } finally {
      for (const id of appointments) await api.delete(`/api/appointments/${id}`)
      await api.request("DELETE", `/api/note-types/custom/${intake}`)
      await api.request("DELETE", `/api/note-types/custom/${followUp}`)
    }
  })
})
