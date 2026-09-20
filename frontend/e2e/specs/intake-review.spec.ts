// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A form comes back, the clinician asks about one answer, and it goes round.
 *
 * The round trip is the point, and it is the one thing no unit test reaches:
 * the patient hands a form in through the browser, the clinician sends one
 * question back, the patient sees the practice's own words on their screen,
 * changes that answer and nothing else, and sends it again. The clinician
 * then writes an answer down for them and accepts.
 *
 * The clinician side is driven through the API. Those routes are the chart's
 * and a browser adds nothing to proving them here; what a browser is
 * uniquely positioned to prove is what the patient is shown after the form
 * comes back, and that is what the page assertions are about.
 *
 * Both factors come from the stand-in their channel is wired to: the link
 * out of the mail server, the step-up code out of the text-message gateway.
 * Both are real sends through the real service; only the last hop is a fake.
 */

import { expect, test } from "../fixtures/auth"
import type { Page } from "@playwright/test"
import type { ApiClient } from "../fixtures/api"
import { firstLink, mail } from "../fixtures/mail"
import { givePatient } from "../fixtures/scenarios"
import { sms, stepUpCode } from "../fixtures/sms"

interface IntakeVersion {
  id: string
  published_at: string | null
}

interface IntakeTemplate {
  id: string
  name: string
  archived_at: string | null
  versions: IntakeVersion[]
}

interface Assignment {
  id: string
  status: string
  progress: { complete: boolean; missing: string[] }
}

interface ReviewItem {
  id: string
  key: string
  value: Record<string, unknown> | null
  provenance: string | null
  superseded_count: number
}

interface ReviewEvent {
  kind: string
  item_ids: string[]
  note_to_patient: string | null
}

interface Review extends Assignment {
  items: ReviewItem[]
  events: ReviewEvent[]
}

const NOTE = "Could you say a bit more about when this started?"
const FIRST_ANSWER = "Panic before every shift."
const REDONE_ANSWER = "Panic before every shift, since about March."
const ENTERED_FOR_PATIENT = "Sleeping through the night."

/** The published version of the form a fresh practice is seeded with. */
async function defaultIntakeVersion(api: ApiClient): Promise<string> {
  const templates = await api.get<IntakeTemplate[]>("/api/intake/templates")
  const intake = templates.find((t) => t.name === "Intake" && t.archived_at === null)
  if (intake === undefined) {
    throw new Error("no Intake form on this practice; every schema is seeded with one")
  }
  const published = intake.versions.filter((v) => v.published_at !== null)
  expect(published.length, "the seeded version ships published").toBeGreaterThan(0)
  return published[0].id
}

/**
 * Invite a patient and sign them in through the shell, as the patient does:
 * open the link the email carried, type the code the text carried.
 */
async function signIn(
  api: ApiClient,
  page: Page,
  patientId: string,
  email: string,
  phone: string,
): Promise<void> {
  await api.post(`/api/patients/${patientId}/portal-invite`)

  const link = firstLink(await mail.waitFor(email))
  const otp = stepUpCode(await sms.waitFor(phone))

  await page.goto(link)
  await page.getByTestId("portal-shell-otp-input").fill(otp)
  await page.getByTestId("portal-shell-otp-submit").click()
  await expect(page.getByTestId("portal-shell-active")).toBeVisible()
}

/**
 * The live portal session token, read off the shell's own store.
 *
 * The key is the shell's (`pablo-portal-session:{slug}`) and the slug is in
 * the URL the invitation link opened, so this reads what the running app
 * wrote rather than minting a second session beside it.
 */
async function portalSessionToken(page: Page): Promise<string> {
  const slug = new URL(page.url()).pathname.split("/").filter(Boolean).pop()
  const raw = await page.evaluate(
    (key) => window.localStorage.getItem(key),
    `pablo-portal-session:${slug}`,
  )
  expect(raw, "the shell stores the session the page signed in with").toBeTruthy()
  return (JSON.parse(raw as string) as { sessionToken: string }).sessionToken
}

/** A complete measure answer: every item scored at the first anchor above zero. */
function everyItemScoredOne(items: number): Record<string, number> {
  return Object.fromEntries(Array.from({ length: items }, (_, i) => [`${i + 1}`, 1]))
}

/** Answer every item of the measure on screen with its first anchor. */
async function answerMeasureOnScreen(page: Page): Promise<void> {
  const groups = page.locator("fieldset[data-testid^='forms-item-']")
  const count = await groups.count()
  expect(count, "a measure renders one group per item").toBeGreaterThan(0)
  for (let index = 0; index < count; index += 1) {
    await groups.nth(index).getByRole("radio").first().check()
  }
}

/** Walk the seeded form end to end and hand it in. */
async function fillTheFormIn(page: Page): Promise<void> {
  await page.getByTestId("forms-list-open").click()

  await page.getByTestId("forms-identity-confirm").click()
  await page.getByTestId("forms-continue").click()

  await page.getByTestId("forms-reason").fill(FIRST_ANSWER)
  await page.getByTestId("forms-continue").click()

  await answerMeasureOnScreen(page)
  await page.getByTestId("forms-continue").click()

  await answerMeasureOnScreen(page)
  await page.getByTestId("forms-continue").click()

  await page.getByTestId("forms-submit").click()
  await expect(page.getByTestId("forms-receipt-code")).toBeVisible()
  await page.getByTestId("forms-receipt-close").click()
}

test.describe("intake review", () => {
  test("a clinician sends one question back and the patient answers it", async ({ api, page }) => {
    const suffix = Date.now().toString(36)
    const email = `review-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1991-06-02" })
    const versionId = await defaultIntakeVersion(api)
    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: versionId },
    )
    const chart = `/api/patients/${patient.id}/intake-assignments/${assigned.id}`

    // --- the patient fills it in ------------------------------------------
    await signIn(api, page, patient.id, email, phone)
    await expect(page.getByTestId("forms-list")).toBeVisible()
    await fillTheFormIn(page)

    const submitted = await api.get<Review>(`${chart}/review`)
    expect(submitted.status).toBe("submitted")
    const reason = submitted.items.find((item) => item.key === "reason")
    const gad7 = submitted.items.find((item) => item.key === "gad7")
    expect(reason, "the seeded form asks why the patient came").toBeDefined()
    expect(gad7, "the seeded form carries the GAD-7").toBeDefined()
    expect(reason!.provenance).toBe("patient")

    // --- the clinician asks about one answer -------------------------------
    const reopened = await api.post<Assignment>(`${chart}/request-correction`, {
      item_ids: [reason!.id],
      note: NOTE,
    })
    expect(reopened.status).toBe("needs_correction")

    // --- the patient sees what was asked, and only that --------------------
    await page.reload()
    await expect(page.getByTestId("forms-list-state")).toContainText("Your clinician")
    await page.getByTestId("forms-list-open").click()

    // The one question they asked about. The rest of the form is not on
    // screen, because the rest of it is settled.
    await expect(page.getByTestId("forms-reason")).toHaveValue(FIRST_ANSWER)
    await expect(page.getByTestId("forms-progress")).toContainText("Question 1 of 1")
    await expect(page.getByTestId("forms-identity-name")).toHaveCount(0)

    await page.getByTestId("forms-reason").fill(REDONE_ANSWER)
    await page.getByTestId("forms-continue").click()

    // Their clinician's own words, on the screen the patient sends from.
    await expect(page.getByTestId("forms-correction-note")).toContainText(NOTE)
    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toBeVisible()

    // --- the chart has both answers, and says which is which ---------------
    const returned = await api.get<Review>(`${chart}/review`)
    expect(returned.status).toBe("submitted")
    const redone = returned.items.find((item) => item.key === "reason")
    expect(redone!.value).toEqual({ text: REDONE_ANSWER })
    expect(redone!.provenance).toBe("patient")
    expect(redone!.superseded_count).toBe(1)
    expect(returned.events.map((event) => event.kind)).toEqual([
      "correction_requested",
      "corrected",
    ])
    expect(returned.events[0].note_to_patient).toBe(NOTE)

    // --- the clinician writes one down, then accepts -----------------------
    await api.post<Assignment>(`${chart}/items/${gad7!.id}/clinician-entry`, {
      value: { item_scores: everyItemScoredOne(7) },
    })
    const accepted = await api.post<Assignment>(`${chart}/accept`, {})
    expect(accepted.status).toBe("accepted")

    const closed = await api.get<Review>(`${chart}/review`)
    const entered = closed.items.find((item) => item.key === "gad7")
    expect(entered!.provenance).toBe("clinician")
    expect(entered!.superseded_count).toBe(1)
    expect(closed.events.map((event) => event.kind)).toEqual([
      "correction_requested",
      "corrected",
      "clinician_entered",
      "accepted",
    ])

    // The form is closed: the same question cannot be reopened now.
    await expect(
      api.post<Assignment>(`${chart}/request-correction`, {
        item_ids: [reason!.id],
        note: "One more thing?",
      }),
    ).rejects.toThrow(/409/)
  })

  test("a form sent back refuses a change to a question nobody asked about", async ({
    api,
    page,
  }) => {
    const suffix = Date.now().toString(36)
    const email = `scope-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1987-11-30" })
    const versionId = await defaultIntakeVersion(api)
    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: versionId },
    )
    const chart = `/api/patients/${patient.id}/intake-assignments/${assigned.id}`

    await signIn(api, page, patient.id, email, phone)
    await fillTheFormIn(page)

    const submitted = await api.get<Review>(`${chart}/review`)
    const reason = submitted.items.find((item) => item.key === "reason")!
    const phq9 = submitted.items.find((item) => item.key === "phq9")!
    await api.post<Assignment>(`${chart}/request-correction`, {
      item_ids: [reason.id],
      note: NOTE,
    })

    // What a browser proves: the portal does not offer the PHQ-9 at all
    // once the form comes back.
    await page.reload()
    await page.getByTestId("forms-list-open").click()
    await expect(page.getByTestId("forms-reason")).toBeVisible()
    await expect(page.getByTestId("forms-progress")).toContainText("Question 1 of 1")

    // And what the screen refuses, the route refuses too — the rule is the
    // server's, and the portal renders it rather than being it. Sent with
    // the patient's own session, the only principal that could make this
    // save, read off the shell's own store.
    const token = await portalSessionToken(page)
    const refused = await page.request.put(
      `${api.baseUrl}/api/patient/intake/assignments/${assigned.id}/items/${phq9.id}`,
      {
        headers: { Authorization: `Bearer ${token}` },
        data: { value: { item_scores: everyItemScoredOne(9) } },
        failOnStatusCode: false,
      },
    )
    expect(refused.status()).toBe(409)

    // And the answer the practice did not ask about is unchanged.
    const unchanged = await api.get<Review>(`${chart}/review`)
    const stillThere = unchanged.items.find((item) => item.key === "phq9")!
    expect(stillThere.superseded_count).toBe(0)
    expect(stillThere.provenance).toBe("patient")
  })
})
