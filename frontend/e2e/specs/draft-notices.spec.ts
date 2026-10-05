// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { Page } from "@playwright/test"

import type { ApiClient } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

// The stack drafts uploaded transcripts through its stand-in
// (NOTE_GENERATION_BASE_URL), which fills every field with "Stand-in draft
// for <section>.<field>." and refuses a transcript carrying its refusal line
// (scripts/fake_llm.py), so both outcomes happen on demand.

const REFUSES_DRAFT = "The stand-in will not draft this session."

// Entered the way the clinician types it. The notice names the session by
// weekday and time, nothing else, read from the session the way every other
// screen reads its date.
const SESSION_DATE = "2026-10-06T14:00"
const SESSION_LABEL = /(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day \d{1,2}:\d{2} [AP]M/

/** The session's weekday and time as this browser shows them. */
async function sessionLabel(page: Page, api: ApiClient, sessionId: string): Promise<string> {
  const { session_date } = await api.get<{ session_date: string }>(`/api/sessions/${sessionId}`)
  return page.evaluate(
    (date) =>
      new Date(date)
        .toLocaleString("en-US", { weekday: "long", hour: "numeric", minute: "2-digit" })
        .replace(" at ", " ")
        .replace(/ /g, " "),
    session_date,
  )
}

function sessionIdFrom(page: Page): string {
  return new URL(page.url()).pathname.split("/").pop() ?? ""
}

async function uploadTranscript(page: Page, patientId: string, transcript: string) {
  await page.goto(`/dashboard/patients/${patientId}`)
  await page.getByRole("button", { name: "New note" }).first().click()
  await page.getByRole("button", { name: /From a transcript/ }).click()
  await page.getByLabel(/Session Date & Time/).fill(SESSION_DATE)
  await page.locator("#transcript_file").setInputFiles({
    name: "session.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(transcript),
  })
  await page.getByRole("button", { name: "Upload & Generate SOAP" }).click()
}

test.describe("draft notices", () => {
  test("a ready draft is announced, and its notice leads to the note", async ({
    api,
    signedInPage: page,
  }) => {
    const patient = await givePatient(api)

    await uploadTranscript(
      page,
      patient.id,
      "[00:00:05] Therapist: How have things been?\n[00:00:09] Client: Steady, thanks.",
    )

    const notice = page.getByTestId("draft-notice")
    await expect(notice).toHaveText(new RegExp(`Draft ready: ${SESSION_LABEL.source} session`))
    const announced = await notice.innerText()
    await expect(notice, "the notice does not name the client").not.toContainText(
      patient.first_name,
    )

    await notice.getByRole("link", { name: "Review draft" }).click()
    await expect(page).toHaveURL(/\/dashboard\/sessions\/[\w-]+$/)
    await expect(page.getByText(/Stand-in draft for/).first()).toBeVisible()
    await expect(notice, "following the notice clears it").toHaveCount(0)
    const label = await sessionLabel(page, api, sessionIdFrom(page))
    expect(announced, "the notice named this session").toContain(`Draft ready: ${label} session`)

    // Announced once: a reload does not raise it again.
    await page.reload()
    await expect(page.getByText(/Stand-in draft for/).first()).toBeVisible()
    await expect(notice).toHaveCount(0)
  })

  test("a failed draft is announced, and its notice leads to the session", async ({
    api,
    signedInPage: page,
  }) => {
    const patient = await givePatient(api)

    await uploadTranscript(page, patient.id, `[00:00:05] Therapist: ${REFUSES_DRAFT}`)

    const notice = page.getByTestId("draft-notice")
    await expect(notice).toHaveText(
      new RegExp(`Couldn't draft the ${SESSION_LABEL.source} session`),
    )
    const announced = await notice.innerText()

    await notice.getByRole("link", { name: "Open session" }).click()
    await expect(page).toHaveURL(/\/dashboard\/sessions\/[\w-]+$/)
    await expect(page.getByText("Note generation failed")).toBeVisible()
    const label = await sessionLabel(page, api, sessionIdFrom(page))
    expect(announced, "the notice named this session").toContain(
      `Couldn't draft the ${label} session`,
    )
  })
})
