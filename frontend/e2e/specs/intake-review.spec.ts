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
 * Both halves are driven through a browser, in two pages of one context: the
 * patient's portal in the page the invitation link opened, the chart in a
 * second one. Asking for corrections and accepting the form are done by
 * clicking them on the chart, so the panel that offers them is proven to be
 * mounted on a page somebody can reach — a route test cannot tell a working
 * route from a screen nobody renders.
 *
 * One clinician action stays at the API, and deliberately. Writing an answer
 * down for somebody in the room is offered on screen as a line of text,
 * which is what a written answer is and what a measure's answer is not: the
 * form's two measures are answered by item scores. So the entry below is
 * made through the route, and what the screen is asked to prove about it is
 * that the chart then says the practice entered it.
 *
 * Both factors come from the stand-in their channel is wired to: the link
 * out of the mail server, the step-up code out of the text-message gateway.
 * Both are real sends through the real service; only the last hop is a fake.
 */

import { expect, test } from "../fixtures/auth"
import type { Locator, Page } from "@playwright/test"
import type { ApiClient } from "../fixtures/api"
import {
  answerMeasureOnScreen,
  defaultIntakeVersion,
  everyItemScoredOne,
  fillTheFormIn,
} from "../fixtures/intake"
import { firstLink, mail } from "../fixtures/mail"
import { givePatient } from "../fixtures/scenarios"
import { sms, stepUpCode } from "../fixtures/sms"

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

/**
 * Open a form's review from the chart's intake card.
 *
 * The card lists every form the patient has been given and opens each one
 * collapsed, so reaching the review is a click rather than a URL — which is
 * the part worth driving: the panel has a page only as long as this row
 * renders it.
 */
async function openReviewOnChart(page: Page, assignmentId: string): Promise<Locator> {
  await page.getByTestId(`intake-assignment-open-${assignmentId}`).click()
  const panel = page.getByTestId("intake-review-panel")
  await expect(panel).toBeVisible()
  return panel
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
    await fillTheFormIn(page, FIRST_ANSWER)

    const submitted = await api.get<Review>(`${chart}/review`)
    expect(submitted.status).toBe("submitted")
    const reason = submitted.items.find((item) => item.key === "reason")
    const gad7 = submitted.items.find((item) => item.key === "gad7")
    const phq9 = submitted.items.find((item) => item.key === "phq9")
    expect(reason, "the seeded form asks why the patient came").toBeDefined()
    expect(gad7, "the seeded form carries the GAD-7").toBeDefined()
    expect(phq9, "the seeded form carries the PHQ-9").toBeDefined()
    expect(reason!.provenance).toBe("patient")

    // --- the clinician asks about one answer, on the chart -----------------
    // A second page in the same context: this one is signed in as the
    // practice, which is what the saved state this context starts from is.
    const chartPage = await page.context().newPage()
    await chartPage.goto(`/dashboard/patients/${patient.id}`)

    const review = await openReviewOnChart(chartPage, assigned.id)
    await expect(review.getByTestId("intake-review-status")).toContainText("Handed in.")
    await expect(review.getByTestId(`intake-review-value-${reason!.id}`)).toContainText(
      FIRST_ANSWER,
    )
    await expect(
      review.getByTestId(`intake-review-provenance-${reason!.id}`),
    ).toContainText("Patient")

    await review.getByTestId(`intake-review-select-${reason!.id}`).check()
    await review.getByTestId("intake-review-note").fill(NOTE)
    await review.getByTestId("intake-review-request").click()

    await expect(review.getByTestId("intake-review-status")).toContainText(
      "Sent back for corrections.",
    )
    const reopened = await api.get<Review>(`${chart}/review`)
    expect(reopened.status).toBe("needs_correction")

    // --- the patient sees what was asked, and only that --------------------
    await page.reload()
    await expect(page.getByTestId("forms-list-state")).toContainText("Your practice")
    await page.getByTestId("forms-list-open").click()

    // The one question they asked about. The rest of the form is not on
    // screen, because the rest of it is settled.
    await expect(page.getByTestId("forms-reason")).toHaveValue(FIRST_ANSWER)
    await expect(page.getByTestId("forms-progress")).toContainText("Question 1 of 1")
    await expect(page.getByTestId("forms-identity-name")).toHaveCount(0)

    // What the screen refuses, the route refuses too — the rule is the
    // server's, and the portal renders it rather than being it. Sent with
    // the patient's own session, the only principal that could make this
    // save, read off the shell's own store.
    const refused = await page.request.put(
      `${api.baseUrl}/api/patient/intake/assignments/${assigned.id}/items/${phq9!.id}`,
      {
        headers: { Authorization: `Bearer ${await portalSessionToken(page)}` },
        data: { value: { item_scores: everyItemScoredOne(9) } },
        failOnStatusCode: false,
      },
    )
    expect(refused.status(), "a question nobody asked about stays settled").toBe(409)

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
    // Through the route, for the reason in this file's opening note: a
    // measure is answered by item scores, and the screen offers a line of
    // text. What the screen is asked for is the sentence it then tells the
    // practice about where that answer came from.
    await api.post<Assignment>(`${chart}/items/${gad7!.id}/clinician-entry`, {
      value: { item_scores: everyItemScoredOne(7) },
    })

    await chartPage.reload()
    const settled = await openReviewOnChart(chartPage, assigned.id)
    await expect(settled.getByTestId(`intake-review-value-${reason!.id}`)).toContainText(
      REDONE_ANSWER,
    )
    await expect(
      settled.getByTestId(`intake-review-provenance-${gad7!.id}`),
    ).toContainText("Entered by practice")
    // The earlier answer is a count on the chart, never the words themselves.
    await settled.getByTestId(`intake-review-earlier-toggle-${reason!.id}`).click()
    await expect(
      settled.getByTestId(`intake-review-earlier-detail-${reason!.id}`),
    ).toContainText("One earlier answer was replaced.")
    await expect(settled.getByTestId("intake-review-items")).not.toContainText(
      FIRST_ANSWER,
    )

    await settled.getByTestId("intake-review-accept").click()
    await expect(settled.getByTestId("intake-review-status")).toContainText("Accepted.")
    // An accepted form offers neither action, because neither is open to it.
    await expect(settled.getByTestId("intake-review-corrections")).toHaveCount(0)

    const accepted = await api.get<Assignment>(`${chart}/review`)
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
})
