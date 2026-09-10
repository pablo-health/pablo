// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Answering what a payer wants, from Settings.
 *
 * An enrollment stops the moment the payer asks for something — here a
 * Medicaid id and a signed agreement — and nothing moves until somebody
 * answers. This drives the whole of that from the browser: read the task,
 * type the id, choose the PDF, send it, and watch the request move on.
 *
 * The parts worth proving are the ones that are invisible from the screen.
 * A PDF reaches the clearinghouse in two requests, not one — a slot, then
 * the bytes at a URL that carries no API key — and the task must not be
 * completed until those bytes have landed. Completing early points the payer
 * at a document that is not there, and the enrollment dies days later for a
 * reason nobody can see from here. So the spec asserts the order of what the
 * clearinghouse actually received, not just that the screen changed.
 */

import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { clearinghouse, type ReceivedRequest } from "../fixtures/clearinghouse"

const MEDICAID_ID = "MD-4471-QA"

/** Enough of a PDF that the clearinghouse will take it. */
const AGREEMENT = Buffer.from("%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n")

const BILLING_PROFILE = {
  legal_name: "Pablo Health Test Provider",
  tax_id: "84-4459714",
  tax_id_type: "ein",
  billing_npi: "1999999984",
  address_line1: "1 Test St",
  city: "Atlanta",
  state: "GA",
  postal_code: "30301",
  phone: "4045550100",
  contact_email: "billing@example.com",
}

interface Payer {
  id: string
}

/**
 * A payer whose remittance enrollment is waiting on the practice.
 *
 * The name carries a stamp because a payer, once added, stays added: the
 * practice outlives the spec and the stack outlives the run, so a fixed name
 * would be two rows the second time through and nothing on screen could tell
 * them apart. The payer *id* is what the clearinghouse's directory is asked
 * about, and that is the same every time.
 */
async function givePayerWaitingOnUs(api: ApiClient, label: string): Promise<string> {
  const name = `${label} ${Date.now().toString(36)}`
  await clearinghouse.reset()
  await api.patch("/api/practice/billing-profile", BILLING_PROFILE)
  const payer = await api.post<Payer>("/api/payers", { name, payer_id: "STEDI" })
  await api.post(`/api/payers/${payer.id}/enrollments`)
  return name
}

/**
 * The document half of what the clearinghouse was asked to do, in order.
 *
 * The enrollment's own id is the vendor's and changes with the fixture, so
 * that one segment is stood down to `{id}`; everything else is asserted as
 * it was sent.
 */
function documentTraffic(requests: ReceivedRequest[]): string[] {
  return requests
    .filter((r) => /(\/documents|\/tasks\/|_fake\/upload)/.test(r.path))
    .map((r) => `${r.method} ${r.path.replace(/enrollments\/[^/]+\//, "enrollments/{id}/")}`)
}

test.describe("answering a payer's enrollment task", () => {
  test("types the id, uploads the agreement, and the request moves on", async ({
    api,
    signedInPage: page,
  }) => {
    const name = await givePayerWaitingOnUs(api, "Stedi Answering")

    await page.goto("/dashboard/settings/insurance")
    await page.getByRole("button", { name: new RegExp(name) }).click()

    // What the payer is waiting for, as a form rather than a paragraph.
    await expect(page.getByText("Needs your action")).toBeVisible()
    await expect(page.getByLabel("Medicaid Provider Identifier")).toBeVisible()
    await expect(
      page.getByRole("link", { name: "Provider Agreement Template" }),
    ).toBeVisible()

    const send = page.getByRole("button", { name: "Send to the payer" })
    await expect(send).toBeDisabled()

    await page.getByLabel("Medicaid Provider Identifier").fill(MEDICAID_ID)
    // Still half an answer: the agreement has not been chosen.
    await expect(send).toBeDisabled()

    await page.getByLabel("Signed Provider Agreement").setInputFiles({
      name: "provider-agreement-signed.pdf",
      mimeType: "application/pdf",
      buffer: AGREEMENT,
    })
    await expect(send).toBeEnabled()
    await send.click()

    // The payer has what it asked for, so there is nothing left to fill in.
    await expect(page.getByText("Nothing outstanding", { exact: false })).toBeVisible()
    await expect(page.getByText("Needs your action")).toHaveCount(0)

    const log = await clearinghouse.received()

    // The order is the point: ask where to put it, put it there, and only
    // then complete the task against it.
    expect(documentTraffic(log.requests)).toEqual([
      "POST /2024-09-01/enrollments/{id}/documents",
      "PUT /_fake/upload/doc-0001",
      "POST /2024-09-01/tasks/task-e2e-0001",
    ])

    const upload = log.requests.find((r) => r.method === "PUT")!
    expect(upload.bytes).toBe(AGREEMENT.byteLength)
    // The bytes go to the storage provider, which has no idea who we are.
    expect(upload.headers.authorization).toBeUndefined()

    const completion = log.requests.find((r) => r.path.includes("/tasks/"))!
    expect(completion.json).toMatchObject({
      responseData: {
        manualTask: {
          values: [
            { key: "MEDICAID_ID", value: { text: MEDICAID_ID } },
            { key: "SIGNED_AGREEMENT", value: { document: { documentId: "doc-0001" } } },
          ],
        },
      },
    })
  })

  test("the uploaded agreement can be read back", async ({ api, signedInPage: page, request }) => {
    const name = await givePayerWaitingOnUs(api, "Stedi Reading Back")

    await page.goto("/dashboard/settings/insurance")
    await page.getByRole("button", { name: new RegExp(name) }).click()
    await page.getByLabel("Medicaid Provider Identifier").fill(MEDICAID_ID)
    await page.getByLabel("Signed Provider Agreement").setInputFiles({
      name: "provider-agreement-signed.pdf",
      mimeType: "application/pdf",
      buffer: AGREEMENT,
    })
    await page.getByRole("button", { name: "Send to the payer" }).click()

    await expect(page.getByText("provider-agreement-signed.pdf")).toBeVisible()

    // Opening it asks for a short-lived link and follows that. Asserted on
    // the link rather than on the new tab: what the browser does with a PDF
    // is the browser's business, and what matters here is that the link we
    // hand it is the clearinghouse's own and that it serves the file.
    const [answer] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/documents/doc-0001")),
      page.getByTestId("enrollment-document-doc-0001").click(),
    ])
    const { url } = (await answer.json()) as { url: string }
    expect(url).toContain("/_fake/download/doc-0001")

    const fetched = await request.get(url)
    expect(fetched.ok()).toBe(true)
    expect((await fetched.body()).subarray(0, 5).toString()).toBe("%PDF-")
  })
})
