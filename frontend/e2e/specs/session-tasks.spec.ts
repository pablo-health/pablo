// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the clinician said in a session they would do, end to end.
 *
 * A SOAP visit proposes no chart updates, but its session is still read for
 * the clinician's own commitments. At sign each one shows under "You said you
 * would", with the day said; the clinician changes the title and adds one,
 * dismisses the other, and signs. The added one is a task of theirs on the
 * client's chart and on the Tasks page; the dismissed one is filed nowhere,
 * and neither is offered again on the signed note.
 *
 * The stack drafts through its stand-in (NOTE_GENERATION_BASE_URL). Its
 * commitment call answers a line "Clinician: Commitment: <task>; <when>;
 * <day>" with that commitment, citing the line.
 */

import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { givePatient } from "../fixtures/scenarios"

type Session = { id: string; status: string }
type Appointment = { id: string }
type Task = { id: string; label: string; due_date: string | null; source: string }

/** A SOAP visit two days ago with the given transcript, drafted and waiting for review. */
async function draftedVisit(
  api: ApiClient,
  patientId: string,
  startAt: Date,
  transcript: string,
  cleanup: string[],
): Promise<Session> {
  const appointment = await api.post<Appointment>("/api/appointments", {
    patient_id: patientId,
    title: "Visit",
    start_at: startAt.toISOString(),
    end_at: new Date(startAt.getTime() + 50 * 60 * 1000).toISOString(),
    duration_minutes: 50,
    session_type: "individual",
    note_type: "soap",
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

test("a commitment from the session, added at sign, is the clinician's task for that client", async ({
  api,
  signedInPage: page,
}) => {
  const patient = await givePatient(api)
  const name = `${patient.first_name} ${patient.last_name}`
  // Noon UTC two days ago, so the visit's day is the same wherever the stack runs.
  const startAt = new Date()
  startAt.setUTCDate(startAt.getUTCDate() - 2)
  startAt.setUTCHours(12, 0, 0, 0)
  const due = new Date(startAt)
  due.setUTCDate(due.getUTCDate() + 3)
  const dueDay = due.toISOString().slice(0, 10)
  const appointments: string[] = []
  let taskId: string | null = null
  try {
    const session = await draftedVisit(
      api,
      patient.id,
      startAt,
      [
        "[00:00:05] Clinician: How did the week go?",
        "[00:00:12] Client: Hard. I want to see someone about medication.",
        `[00:00:20] Clinician: Commitment: Send a referral to the psychiatrist; by Friday; ${dueDay}`,
        "[00:00:31] Clinician: Commitment: Look for a sleep workbook;;",
        "[00:00:40] Client: I'll keep the thought log every night.",
      ].join("\n"),
      appointments,
    )

    await page.goto(`/dashboard/sessions/${session.id}`)
    await page.getByRole("button", { name: "Sign and lock" }).click()
    const dialog = page.getByRole("dialog")
    await expect(dialog.getByRole("heading", { name: "You said you would" })).toBeVisible()
    // A SOAP note updates no chart: there is no chart step, only the tasks.
    await expect(dialog.getByRole("heading", { name: "Update the chart" })).toHaveCount(0)

    const rows = dialog.getByTestId("session-task-proposal")
    await expect(rows).toHaveCount(2)
    const referral = rows.filter({ hasText: "by Friday" })
    await expect(referral.getByLabel("Due date")).toHaveValue(dueDay)
    await referral.getByRole("textbox", { name: "Task" }).fill("Fax the psychiatry referral")
    const decided = page.waitForResponse(
      (r) => r.url().includes("/chart-proposals/") && r.request().method() === "POST",
    )
    await referral.getByRole("button", { name: "Add to my tasks" }).click()
    expect((await decided).status()).toBe(200)
    // The sign step lists what is still undecided: the added one leaves it.
    await expect(rows).toHaveCount(1)
    await rows.getByRole("button", { name: "Dismiss" }).click()
    await expect(dialog.getByRole("heading", { name: "You said you would" })).toHaveCount(0)

    await dialog.getByRole("button", { name: "Sign and lock" }).click()
    await expect
      .poll(async () => (await api.get<Session>(`/api/sessions/${session.id}`)).status)
      .toBe("finalized")

    // One task, the clinician's, tied to the client, with their title and the day said.
    const tasks = await api.get<Task[]>(`/api/tasks?patient_id=${patient.id}`)
    expect(tasks.map((t) => [t.label, t.due_date, t.source])).toEqual([
      ["Fax the psychiatry referral", dueDay, "session"],
    ])
    taskId = tasks[0].id

    // On the client's chart, and named on the Tasks page.
    await page.goto(`/dashboard/patients/${patient.id}`)
    await expect(
      page.getByTestId("client-tasks").getByTestId(`compliance-item-${taskId}`),
    ).toContainText("Fax the psychiatry referral")
    await page.goto(`/dashboard/settings/tasks?item=${taskId}`)
    await expect(page.getByTestId(`compliance-item-${taskId}`)).toContainText(name)

    // The signed note keeps what was decided and offers neither again.
    await page.goto(`/dashboard/sessions/${session.id}`)
    const kept = page.getByTestId("session-tasks")
    await expect(kept.getByText("Added to your tasks")).toBeVisible()
    await expect(kept.getByRole("button", { name: "Add to my tasks" })).toHaveCount(0)
  } finally {
    if (taskId) await api.request("DELETE", `/api/tasks/${taskId}`).catch(() => undefined)
    for (const id of appointments) await api.delete(`/api/appointments/${id}`)
  }
})
