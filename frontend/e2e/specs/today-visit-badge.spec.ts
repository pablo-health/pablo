// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Today on the dashboard shows where a visit is, not that it was booked.
 *
 * Starting a session links it to the appointment and leaves the appointment
 * confirmed; the badge follows the linked session from there: in session,
 * a drafted note to review, then signed. The stack drafts through its
 * stand-in, so a transcript reaches review on its own.
 */

import { randomBytes } from "node:crypto"
import type { Page } from "@playwright/test"

import type { ApiClient } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Session = { id: string; status: string }

const VISIT = [
  "[00:00:05] Therapist: How has the week been since we last met?",
  "[00:01:10] Client: Calmer. The breathing exercise helped before meetings.",
  "[00:04:30] Therapist: Good. Keep practising it, and we will meet next week.",
].join("\n")

// Early on the browser's own calendar day, so the visit is on Today whatever
// the hour the suite runs; a random minute keeps reruns from overlapping.
async function bookToday(page: Page, api: ApiClient, patientId: string, title: string) {
  const minute = Math.floor(Math.random() * 50)
  const startAt = await page.evaluate((m) => {
    const d = new Date()
    d.setHours(5, m, 0, 0)
    return d.toISOString()
  }, minute)
  const endAt = new Date(new Date(startAt).getTime() + 5 * 60 * 1000).toISOString()
  return api.post<{ id: string }>("/api/appointments", {
    patient_id: patientId,
    title,
    start_at: startAt,
    end_at: endAt,
    duration_minutes: 5,
    session_type: "individual",
  })
}

async function badgeOnToday(page: Page, title: string) {
  await page.goto("/dashboard")
  return page.getByRole("listitem").filter({ hasText: title })
}

test("a started visit's badge follows its session through review and signing", async ({
  signedInPage: page,
  api,
}) => {
  const title = `Badge visit ${randomBytes(3).toString("hex")}`
  const patient = await givePatient(api)
  await page.goto("/dashboard")
  const appointment = await bookToday(page, api, patient.id, title)

  let row = await badgeOnToday(page, title)
  await expect(row).toContainText("Scheduled")

  const session = await api.post<Session>(
    `/api/appointments/${appointment.id}/start-session`,
    {},
  )
  await api.patch(`/api/sessions/${session.id}/status`, { status: "in_progress" })
  row = await badgeOnToday(page, title)
  await expect(row).toContainText("In session")
  await expect(row).not.toContainText("Scheduled")

  await api.patch(`/api/sessions/${session.id}/status`, { status: "recording_complete" })
  await api.post(`/api/sessions/${session.id}/transcript`, { format: "txt", content: VISIT })
  await expect
    .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status, {
      timeout: 30_000,
    })
    .toBe("pending_review")
  row = await badgeOnToday(page, title)
  await expect(row).toContainText("To review")

  await api.patch(`/api/sessions/${session.id}/finalize`, {})
  row = await badgeOnToday(page, title)
  await expect(row).toContainText("Signed")
  await expect(row.getByRole("link", { name: /^open$/i })).toHaveAttribute(
    "href",
    `/dashboard/sessions/${session.id}`,
  )
})
