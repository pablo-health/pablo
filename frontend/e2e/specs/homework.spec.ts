// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Homework over the real stack. A note's plan suggests homework beside its
 * other chart updates, decided in the same review at signing; one left
 * undecided waits on the chart with the rest. The clinician accepts one as
 * written, edits one and dismisses one, then adds one by hand; the client
 * sees only what was assigned and marks one done with a note; the chart's
 * Homework tab shows it done, with the note.
 *
 * The plan is a SOAP note's homework list, read without a model and brought
 * up to date when the note is edited, so nothing here depends on the
 * drafting stand-in's wording.
 *
 * The isolation claims an end-to-end test is placed to make are made over
 * real HTTP: a suggestion is not on the client's list until accepted, and a
 * second client cannot see or mark the first one's homework.
 *
 * Needs `homework` in the stack's `PORTAL_MODULES` (docker-compose.e2e.yml).
 */

import type { Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import {
  givePortalContactDetails,
  givePortalInvitation,
  givePortalSession,
  openPortalSection,
  signInToPortal,
} from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

const PATIENT_HOMEWORK = "/api/patient/homework"

interface PatientHomework {
  id: string
  text: string
  status: "assigned" | "done"
  client_note: string | null
}

interface HomeworkItem {
  id: string
  text: string
  status: string
}

interface Overview {
  assignments: HomeworkItem[]
}

interface ChartUpdates {
  groups: {
    note_id: string
    proposals: { id: string; field_key: string; item_key: string; proposed_text: string }[]
  }[]
}

let visits = 0

/**
 * A SOAP note's edited body as the editor saves it: one narrative string per
 * section, each field under its bold label, a list field as bullets.
 */
function soapWithHomework(homework: string[]) {
  return {
    subjective: "**Chief Complaint:** Worry about work presentations.",
    objective: "**Affect Observed:** Anxious, congruent.",
    assessment: "**Progress:** Using thought records with some success.",
    plan: [
      "**Interventions Used:**\n- Cognitive restructuring.",
      `**Homework Assignments:**\n${homework.map((t) => `- ${t}`).join("\n")}`,
      "**Next Steps:**\n- Review homework next session.",
    ].join("\n\n"),
  }
}

/** A visit that happened, with a SOAP note whose plan lists homework, signed undecided. */
async function giveSignedSoapNote(
  api: ApiClient,
  patientId: string,
  homework: string[],
): Promise<string> {
  visits += 1
  const startAt = new Date(Date.now() - (24 + visits) * 60 * 60 * 1000)
  const appointment = await api.post<{ id: string }>("/api/appointments", {
    patient_id: patientId,
    title: "Session",
    start_at: startAt.toISOString(),
    end_at: new Date(startAt.getTime() + 50 * 60 * 1000).toISOString(),
    duration_minutes: 50,
    session_type: "individual",
  })
  const session = await api.post<{ id: string }>(
    `/api/appointments/${appointment.id}/start-session`,
    {},
  )
  const detail = await api.get<{ note: { id: string } | null }>(`/api/sessions/${session.id}`)
  const noteId = detail.note?.id
  if (!noteId) throw new Error(`started session ${session.id} has no note`)
  await api.patch(`/api/notes/${noteId}`, { content_edited: soapWithHomework(homework) })
  await api.post(`/api/notes/${noteId}/finalize`, {})
  return noteId
}

async function openHomeworkTab(page: Page, patientId: string): Promise<void> {
  await page.goto(`/dashboard/patients/${patientId}`)
  await page.getByRole("tab", { name: /Homework/ }).click()
}

test("a signed note suggests homework, the clinician assigns it, the client marks it done @portal", async ({
  api,
  signedInPage: page,
  browser,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  await giveSignedSoapNote(api, patient.id, [
    "Client will complete two thought records about presentations.",
    "Practice paced breathing before meetings.",
    "Call a friend this weekend.",
    "None assigned.",
  ])

  // Signed with its homework undecided, so it waits with the chart's updates.
  await page.goto(`/dashboard/patients/${patient.id}`)
  const waiting = page.getByRole("region", { name: "Chart updates waiting" })
  const homework = waiting.getByRole("region", { name: "Homework" })
  const suggestions = homework.getByTestId("chart-proposal")
  await expect(suggestions).toHaveCount(3)
  const records = suggestions.filter({ hasText: "Complete two thought records about presentations." })
  await expect(records).toContainText(
    "In the plan: “Client will complete two thought records about presentations.”",
  )

  // Accept one as written, edit one, dismiss the third, then add one by hand.
  await records.getByRole("button", { name: "Accept" }).click()
  await expect(suggestions).toHaveCount(2)
  const breathing = suggestions.filter({ hasText: "Practice paced breathing before meetings." })
  await breathing.getByRole("button", { name: "Edit" }).click()
  await breathing
    .getByLabel("Edit Homework: Assignment")
    .fill("Practice paced breathing before each meeting")
  await breathing.getByRole("button", { name: "Save to chart" }).click()
  await expect(suggestions).toHaveCount(1)
  await suggestions.getByRole("button", { name: "Discard" }).click()
  await expect(waiting).toHaveCount(0)

  await page.getByRole("tab", { name: /Homework/ }).click()
  await expect(page.getByTestId("homework-assignment")).toHaveCount(2)
  await page.getByLabel("Add homework").fill("Write down one thing that went well each day")
  await page.getByRole("button", { name: "Add homework" }).click()
  await expect(page.getByTestId("homework-assignment")).toHaveCount(3)
  await expect(page.getByTestId("homework-progress")).toHaveText("0 of 3 done")
  await expect(page.getByTestId("homework-assignment")).not.toContainText(["Call a friend"])

  // The client signs in to the portal and sees exactly what was assigned.
  const invitation = await givePortalInvitation(api, patient.id, email, phone)
  const clientContext = await browser.newContext()
  const portal = await clientContext.newPage()
  try {
    await signInToPortal(portal, invitation)
    await expect(portal.getByTestId("portal-home-tile-homework")).toContainText("3 to do")
    await openPortalSection(portal, "homework")
    const items = portal.locator('[data-testid^="portal-homework-item-"]')
    await expect(items).toHaveCount(3)
    await expect(portal.getByTestId("portal-homework")).toContainText(
      "Practice paced breathing before each meeting",
    )

    const breathing = items.filter({ hasText: "Practice paced breathing before each meeting" })
    await breathing.getByRole("button", { name: "Mark done" }).click()
    await breathing.getByLabel(/Anything to tell your clinician/).fill("Did it before two meetings")
    await breathing.getByRole("button", { name: "Mark done" }).click()
    await expect(breathing).toHaveAttribute("data-status", "done")
    await expect(breathing).toContainText("Did it before two meetings")
  } finally {
    await clientContext.close()
  }

  // Back on the chart: one of three done, with the client's own words.
  await openHomeworkTab(page, patient.id)
  await expect(page.getByTestId("homework-progress")).toHaveText("1 of 3 done")
  await expect(page.getByTestId("homework-client-note")).toHaveText("“Did it before two meetings”")
})

test("a suggestion is not the client's, and one client cannot reach another's homework @portal", async ({
  api,
  request,
}) => {
  const first = givePortalContactDetails()
  const firstPatient = await givePatient(api, first)
  await giveSignedSoapNote(api, firstPatient.id, [
    "Keep a worry log.",
    "Walk after dinner.",
  ])
  const second = givePortalContactDetails()
  const secondPatient = await givePatient(api, second)

  const waiting = await api.get<ChartUpdates>(`/api/patients/${firstPatient.id}/chart-proposals`)
  const suggestions = waiting.groups
    .flatMap((g) => g.proposals.map((p) => ({ ...p, noteId: g.note_id })))
    .filter((p) => p.field_key === "homework")
    .sort((x, y) => x.item_key.localeCompare(y.item_key))
  expect(suggestions.map((p) => p.proposed_text)).toEqual(["Keep a worry log.", "Walk after dinner."])
  const [accepted, pending] = suggestions
  await api.post(`/api/notes/${accepted.noteId}/chart-proposals/${accepted.id}/decision`, {
    decision: "accept",
  })
  const assigned = (
    await api.get<Overview>(`/api/patients/${firstPatient.id}/homework`)
  ).assignments[0]

  const firstHeaders = {
    Authorization: `Bearer ${await givePortalSession(api, request, firstPatient.id, first.email, first.phone)}`,
  }
  const secondHeaders = {
    Authorization: `Bearer ${await givePortalSession(api, request, secondPatient.id, second.email, second.phone)}`,
  }

  const mine = (await (
    await request.get(`${BACKEND_URL}${PATIENT_HOMEWORK}`, { headers: firstHeaders })
  ).json()) as { data: PatientHomework[] }
  expect(mine.data.map((h) => h.id), "only what the clinician assigned").toEqual([assigned.id])
  expect(Object.keys(mine.data[0]).sort()).toEqual(
    ["assigned_at", "client_note", "completed_at", "id", "status", "text"].sort(),
  )

  const suggestionMarked = await request.post(
    `${BACKEND_URL}${PATIENT_HOMEWORK}/${pending.id}/done`,
    { headers: firstHeaders, data: {} },
  )
  expect(suggestionMarked.status(), "a suggestion answers as if it did not exist").toBe(404)

  const theirs = (await (
    await request.get(`${BACKEND_URL}${PATIENT_HOMEWORK}`, { headers: secondHeaders })
  ).json()) as { data: PatientHomework[] }
  expect(theirs.data).toEqual([])
  const crossMarked = await request.post(`${BACKEND_URL}${PATIENT_HOMEWORK}/${assigned.id}/done`, {
    headers: secondHeaders,
    data: {},
  })
  expect(crossMarked.status()).toBe(404)
})


test("homework is decided at signing, with the note's other chart updates", async ({
  api,
  signedInPage: page,
}) => {
  const patient = await givePatient(api)
  const session = await api.post<{ id: string }>(`/api/patients/${patient.id}/sessions/upload`, {
    patient_id: patient.id,
    session_date: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    transcript: {
      format: "txt",
      content: "[00:00:05] Therapist: How did the week go?\n[00:00:09] Client: Busier than usual.",
    },
  })
  await expect
    .poll(async () => (await api.get<{ status: string }>(`/api/sessions/${session.id}`)).status, {
      timeout: 30_000,
    })
    .toBe("pending_review")
  const detail = await api.get<{ note: { id: string } | null }>(`/api/sessions/${session.id}`)
  const noteId = detail.note?.id
  if (!noteId) throw new Error(`session ${session.id} has no note`)

  // The draft's homework is suggested as drafted; the clinician's edits to the
  // plan are what the review ends up suggesting.
  for (const homework of [
    ["Keep a worry log.", "Walk after dinner."],
    ["Keep a worry log each evening.", "Walk after dinner."],
  ]) {
    await api.patch(`/api/notes/${noteId}`, { content_edited: soapWithHomework(homework) })
  }

  await page.goto(`/dashboard/sessions/${session.id}`)
  await page.getByRole("button", { name: "Sign and lock" }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByRole("heading", { name: "Update the chart" })).toBeVisible()
  const homework = dialog.getByRole("region", { name: "Homework" })
  const items = homework.getByTestId("chart-proposal")
  await expect(items).toHaveCount(2)
  await expect(homework).toContainText("Keep a worry log each evening.")
  await expect(homework).not.toContainText("Keep a worry log.")

  await items.filter({ hasText: "Keep a worry log each evening." }).getByRole("button", {
    name: "Accept",
  }).click()
  await items.filter({ hasText: "Walk after dinner." }).getByRole("button", {
    name: "Discard",
  }).click()
  await expect(dialog.getByRole("heading", { name: "Update the chart" })).toBeHidden()
  await dialog.getByRole("button", { name: "Sign and lock" }).click()
  await expect
    .poll(async () => (await api.get<{ status: string }>(`/api/sessions/${session.id}`)).status)
    .toBe("finalized")

  const assigned = await api.get<Overview>(`/api/patients/${patient.id}/homework`)
  expect(assigned.assignments.map((a) => [a.text, a.status])).toEqual([
    ["Keep a worry log each evening.", "assigned"],
  ])
})
