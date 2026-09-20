// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A form is filled in, corrected, and taken out of the product as one file.
 *
 * The file is the point, and it is the one thing no unit test reaches: a
 * patient answers through the browser, the clinician sends one question
 * back, the patient answers it again, the clinician writes one down — and
 * only then is the document asked for. Every claim below is checked against
 * the bytes that come back over HTTP, so what is proven is the document a
 * practice would actually file rather than a renderer's opinion of one.
 *
 * The clinician side is driven through the API, like the review spec beside
 * it: those routes are the chart's, and a browser adds nothing to proving
 * them. What the browser is here for is the answers — they have to be the
 * patient's own, typed into the patient's own screens, or the provenance
 * the document prints would be a fixture rather than a fact.
 */

import type { APIRequestContext } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { signInWithPassword } from "../fixtures/api"
import { defaultIntakeVersion, everyItemScoredOne, fillTheFormIn } from "../fixtures/intake"
import { givePortalContactDetails, givePortalInvitation, signInToPortal } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"

interface Assignment {
  id: string
  status: string
  receipt_code: string | null
}

interface ReviewItem {
  id: string
  key: string
}

interface Review extends Assignment {
  items: ReviewItem[]
}

interface ExportedFile {
  status: number
  body: string
  disposition: string | undefined
  type: string | undefined
}

const NOTE = "Could you say a bit more about when this started?"
const FIRST_ANSWER = "Panic before every shift."
const REDONE_ANSWER = "Panic before every shift, since about March."

/**
 * Fetch the document with the clinician's own bearer token.
 *
 * The shared API client parses what it gets as JSON and this route sends a
 * document, so the request is made directly — which is also the only way to
 * assert on the bytes and on the headers that make a browser save them.
 */
async function exportTheForm(
  request: APIRequestContext,
  idToken: string,
  url: string,
): Promise<ExportedFile> {
  const response = await request.get(url, {
    headers: { Authorization: `Bearer ${idToken}` },
    failOnStatusCode: false,
  })
  return {
    status: response.status(),
    body: await response.text(),
    disposition: response.headers()["content-disposition"],
    type: response.headers()["content-type"],
  }
}

test.describe("intake export", () => {
  test("the whole form comes back as one document a practice can file", async ({
    api,
    onboardedUser,
    page,
    request,
  }) => {
    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(api, { email, phone, date_of_birth: "1991-06-02" })
    const versionId = await defaultIntakeVersion(api)
    const assigned = await api.post<Assignment>(`/api/patients/${patient.id}/intake-assignments`, {
      version_id: versionId,
    })
    const chart = `/api/patients/${patient.id}/intake-assignments/${assigned.id}`
    const exportUrl = `${api.baseUrl}${chart}/export`

    // --- the patient fills it in ------------------------------------------
    await signInToPortal(page, await givePortalInvitation(api, patient.id, email, phone))
    await fillTheFormIn(page, FIRST_ANSWER)

    const submitted = await api.get<Review>(`${chart}/review`)
    expect(submitted.status).toBe("submitted")
    const reason = submitted.items.find((item) => item.key === "reason")
    const gad7 = submitted.items.find((item) => item.key === "gad7")
    expect(reason, "the seeded form asks why the patient came").toBeDefined()
    expect(gad7, "the seeded form carries the GAD-7").toBeDefined()

    // --- one question goes back, and comes back answered again -------------
    await api.post<Assignment>(`${chart}/request-correction`, {
      item_ids: [reason!.id],
      note: NOTE,
    })
    await page.reload()
    await page.getByTestId("forms-list-open").click()
    await expect(page.getByTestId("forms-reason")).toHaveValue(FIRST_ANSWER)
    await page.getByTestId("forms-reason").fill(REDONE_ANSWER)
    await page.getByTestId("forms-continue").click()
    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toBeVisible()

    // --- and the practice writes one down ----------------------------------
    await api.post<Assignment>(`${chart}/items/${gad7!.id}/clinician-entry`, {
      value: { item_scores: everyItemScoredOne(7) },
    })

    // --- the document ------------------------------------------------------
    const returned = await api.get<Review>(`${chart}/review`)
    const receipt = returned.receipt_code as string
    expect(receipt, "a handed-in form has a receipt").toBeTruthy()

    const idToken = await signInWithPassword(onboardedUser.email, onboardedUser.password)
    const file = await exportTheForm(request, idToken, exportUrl)
    expect(file.status, `the export is served (${file.status})`).toBe(200)
    expect(file.disposition).toBe(`attachment; filename="intake-${receipt}.html"`)
    expect(file.type).toContain("text/html")

    // What the practice would read off the printed page: the answer that
    // stands, the one it replaced, who wrote each, and why it was asked for.
    expect(file.body).toContain(receipt)
    expect(file.body).toContain(REDONE_ANSWER)
    expect(file.body).toContain(FIRST_ANSWER)
    expect(file.body).toContain("Answered by the patient")
    expect(file.body).toContain("Replaced")
    expect(file.body).toContain("Entered by the practice")
    expect(file.body).toContain(NOTE)
    expect(file.body).toContain("GAD-7:")

    // Self-contained: nothing to run, nothing to fetch.
    expect(file.body.toLowerCase()).not.toContain("<script")
    expect(file.body).not.toContain("src=")

    // The same form, the same bytes — which is what makes one copy
    // checkable against another.
    const again = await exportTheForm(request, idToken, exportUrl)
    expect(again.body).toBe(file.body)

    // A form on somebody else's chart is not found, so the path cannot be
    // used to find out whose form an id names.
    const stranger = await givePatient(api, givePortalContactDetails())
    const elsewhere = await exportTheForm(
      request,
      idToken,
      `${api.baseUrl}/api/patients/${stranger.id}/intake-assignments/${assigned.id}/export`,
    )
    expect(elsewhere.status).toBe(404)
  })
})
