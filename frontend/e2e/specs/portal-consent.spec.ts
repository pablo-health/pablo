// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A patient reads a consent document in a browser and signs it.
 *
 * `portal-forms.spec.ts` proves the walk; this proves the one question on it
 * that is not answered by typing into a box. What a browser is uniquely
 * positioned to show here is that the document a practice wrote reaches the
 * person being asked to agree to it, that Sign is not offered until they
 * have both read and typed, that a signed form can then be handed in, and
 * that the signature is still there when the page is reloaded.
 *
 * The refusals are checked through the API beside it, because a browser
 * that has already signed has no way to press the button twice.
 *
 * Both factors come from the stand-in their channel is wired to: the link
 * out of the mail server, the step-up code out of the text-message gateway.
 */

import { expect, test } from "../fixtures/auth"
import type { Page } from "@playwright/test"
import type { ApiClient } from "../fixtures/api"
import { firstLink, mail } from "../fixtures/mail"
import { givePatient } from "../fixtures/scenarios"
import { sms, stepUpCode } from "../fixtures/sms"
import { BACKEND_URL } from "../fixtures/stack"

const REDEEM_PATH = "/api/patient/auth/redeem"

const CONSENT_TITLE = "Consent to treatment"
const CONSENT_SENTENCE = "I agree to begin treatment at this practice."
const CONSENT_CLAUSE = "Either of us may end treatment at any time."
const CONSENT_BODY = `${CONSENT_SENTENCE}\n\n- ${CONSENT_CLAUSE}`

interface IntakeDocument {
  id: string
  document_key: string
  title: string
  version: number
  digest: string
  published_at: string | null
  signer_roles: string[]
}

interface IntakeVersion {
  id: string
  published_at: string | null
}

interface IntakeTemplate {
  id: string
  name: string
  versions: IntakeVersion[]
}

interface IntakeItem {
  id: string
  key: string
  item_type: string
}

interface IntakeVersionDetail extends IntakeVersion {
  template_id: string
  items: IntakeItem[]
}

interface Assignment {
  id: string
  status: string
  receipt_code: string | null
  progress: { complete: boolean; missing: string[] }
}

interface Signature {
  id: string
  item_id: string
  document_version_id: string
  document_digest: string
  signer_role: string
  signer_typed_name: string
  consent_statement: string
  auth_strength: string
  evidence_digest: string
}

/** Write a consent document and publish it, through the practice's own API. */
async function publishDocument(
  api: ApiClient,
  signerRoles: string[] = ["patient"],
): Promise<IntakeDocument> {
  const draft = await api.post<IntakeDocument>("/api/intake/documents", {
    title: `${CONSENT_TITLE} ${Date.now().toString(36)}`,
    body_markdown: CONSENT_BODY,
    signer_roles: signerRoles,
  })
  const published = await api.post<IntakeDocument>(`/api/intake/documents/${draft.id}/publish`)
  expect(published.published_at).not.toBeNull()
  return published
}

/**
 * A form whose only question is that document, published.
 *
 * Publishing is what pins the document's current version onto the item, so
 * the assignment always knows exactly which words the patient was shown.
 */
async function publishConsentForm(
  api: ApiClient,
  document: IntakeDocument,
  resignOnNewVersion = false,
): Promise<{ versionId: string; itemId: string }> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `Paperwork ${Date.now().toString(36)}`,
  })
  const draftId = template.versions[0].id

  await api.put<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/items`,
    {
      items: [
        {
          key: "consent",
          item_type: "consent_document",
          resign_on_new_version: resignOnNewVersion,
          config: { document_key: document.document_key },
        },
      ],
    },
  )

  const published = await api.post<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
  expect(published.published_at).not.toBeNull()
  const consent = published.items.find((item) => item.item_type === "consent_document")
  expect(consent, "the published form carries the consent question").toBeDefined()
  return { versionId: published.id, itemId: (consent as IntakeItem).id }
}

/** Invite a patient and sign them in through the shell, as the patient does. */
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

test.describe("portal consent signatures", () => {
  test("a patient reads a consent document, signs it and hands the form in", async ({
    api,
    page,
  }) => {
    const suffix = Date.now().toString(36)
    const email = `consent-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1988-11-02" })
    const document = await publishDocument(api)
    const { versionId, itemId } = await publishConsentForm(api, document)

    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: versionId },
    )
    expect(assigned.progress.complete).toBe(false)
    expect(assigned.progress.missing).toEqual([itemId])

    await signIn(api, page, patient.id, email, phone)

    await expect(page.getByTestId("forms-list-state")).toContainText("1 question left")
    await page.getByTestId("forms-list-open").click()

    // The words the practice wrote reach the person being asked to agree,
    // including the bullet, which only the server's renderer produces.
    await expect(page.getByTestId("forms-consent-document")).toContainText(CONSENT_SENTENCE)
    await expect(page.getByTestId("forms-consent-document")).toContainText(CONSENT_CLAUSE)

    // And the sentence they are agreeing under, served rather than written
    // by the browser.
    await expect(page.getByTestId("forms-consent-statement")).toContainText(
      "electronic signature",
    )

    // Sign is not offered until both halves are done.
    await expect(page.getByTestId("forms-consent-sign")).toBeDisabled()
    await page.getByTestId("forms-consent-affirm").check()
    await expect(page.getByTestId("forms-consent-sign")).toBeDisabled()
    await page.getByTestId("forms-consent-name").fill("Ada Lovelace")
    await expect(page.getByTestId("forms-consent-sign")).toBeEnabled()

    await page.getByTestId("forms-consent-sign").click()

    // The signed state names who signed, and the button is gone.
    await expect(page.getByTestId("forms-consent-signed")).toContainText("Ada Lovelace")
    await expect(page.getByTestId("forms-consent-sign")).toHaveCount(0)

    // --- the tab is reloaded ------------------------------------------------
    // Nothing was kept in this browser, so a form that still looks signed is
    // reading the server's own answer.
    await page.reload()
    await expect(page.getByTestId("forms-list-state")).toContainText("Ready to send")
    await page.getByTestId("forms-list-open").click()
    await expect(page.getByTestId("forms-consent-signed")).toContainText("Ada Lovelace")

    // --- check, then send ---------------------------------------------------
    await page.getByTestId("forms-continue").click()
    await expect(page.getByTestId("forms-review")).toBeVisible()
    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toHaveText(/^[2-9A-HJ-NP-TV-Z]{8}$/)

    // --- and the evidence is what was recorded ------------------------------
    const onChart = await api.get<Assignment>(
      `/api/patients/${patient.id}/intake-assignments/${assigned.id}`,
    )
    expect(onChart.status).toBe("submitted")
    expect(onChart.progress.complete).toBe(true)
  })

  test("signing twice is refused, and the recorded evidence names the version", async ({
    api,
    request,
  }) => {
    const suffix = Date.now().toString(36)
    const email = `consent-api-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1979-04-09" })
    const document = await publishDocument(api, ["patient", "guardian"])
    const { versionId, itemId } = await publishConsentForm(api, document)

    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: versionId },
    )

    const session = await portalSession(api, request, patient.id, email, phone)
    const signatures = `${BACKEND_URL}/api/patient/intake/assignments/${assigned.id}/signatures`
    const auth = { Authorization: `Bearer ${session}` }

    const first = await request.post(signatures, {
      headers: auth,
      data: { item_id: itemId, signer_role: "patient", typed_name: "Grace Hopper", affirm: true },
    })
    expect(first.status()).toBe(201)
    const recorded = (await first.json()) as Signature

    // The evidence names the exact revision that was on the screen, and
    // carries that revision's own digest.
    expect(recorded.document_version_id).toBe(document.id)
    expect(recorded.document_digest).toBe(document.digest)
    expect(recorded.signer_role).toBe("patient")
    expect(recorded.signer_typed_name).toBe("Grace Hopper")
    expect(recorded.auth_strength).toBe("stepped_up")
    expect(recorded.evidence_digest).toMatch(/^[0-9a-f]{64}$/)

    // The same role again is refused rather than recorded twice.
    const again = await request.post(signatures, {
      headers: auth,
      data: { item_id: itemId, signer_role: "patient", typed_name: "Grace Hopper", affirm: true },
    })
    expect(again.status()).toBe(409)

    // A guardian signature on a document that asks for one is a different
    // signature, not a second attempt at the same one.
    const guardian = await request.post(signatures, {
      headers: auth,
      data: {
        item_id: itemId,
        signer_role: "guardian",
        typed_name: "Mary Somerville",
        affirm: true,
      },
    })
    expect(guardian.status()).toBe(201)
    expect(((await guardian.json()) as Signature).signer_role).toBe("guardian")

    // Both are on the record, and the form is now finished.
    const listed = await request.get(signatures, { headers: auth })
    expect(listed.status()).toBe(200)
    const rows = (await listed.json()) as Signature[]
    expect(rows.map((row) => row.signer_role).sort()).toEqual(["guardian", "patient"])

    const onChart = await api.get<Assignment>(
      `/api/patients/${patient.id}/intake-assignments/${assigned.id}`,
    )
    expect(onChart.progress.complete).toBe(true)
  })

  test("an unaffirmed or unnamed signature is refused", async ({ api, request }) => {
    const suffix = Date.now().toString(36)
    const email = `consent-refuse-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1992-06-30" })
    const document = await publishDocument(api)
    const { versionId, itemId } = await publishConsentForm(api, document)
    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: versionId },
    )

    const session = await portalSession(api, request, patient.id, email, phone)
    const signatures = `${BACKEND_URL}/api/patient/intake/assignments/${assigned.id}/signatures`
    const auth = { Authorization: `Bearer ${session}` }

    const unaffirmed = await request.post(signatures, {
      headers: auth,
      data: { item_id: itemId, signer_role: "patient", typed_name: "Ada Lovelace", affirm: false },
    })
    expect(unaffirmed.status()).toBe(422)

    const unnamed = await request.post(signatures, {
      headers: auth,
      data: { item_id: itemId, signer_role: "patient", typed_name: "   ", affirm: true },
    })
    expect(unnamed.status()).toBe(422)

    // A role this document does not ask for is refused too.
    const wrongRole = await request.post(signatures, {
      headers: auth,
      data: {
        item_id: itemId,
        signer_role: "guardian",
        typed_name: "Mary Somerville",
        affirm: true,
      },
    })
    expect(wrongRole.status()).toBe(422)

    // And nothing was recorded by any of them.
    const listed = await request.get(signatures, { headers: auth })
    expect((await listed.json()) as Signature[]).toEqual([])
  })
})

/**
 * A stepped-up portal session token for this patient.
 *
 * The browser gets one by opening the link and typing the code; a request
 * context redeems both factors in the one call the portal offers. Same two
 * factors, read from the stand-in their channel is wired to.
 */
async function portalSession(
  api: ApiClient,
  request: import("@playwright/test").APIRequestContext,
  patientId: string,
  email: string,
  phone: string,
): Promise<string> {
  await api.post(`/api/patients/${patientId}/portal-invite`)

  const link = firstLink(await mail.waitFor(email))
  const token = new URLSearchParams(new URL(link).hash.slice(1)).get("invite")
  const otp = stepUpCode(await sms.waitFor(phone))

  const redeemed = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token, otp },
  })
  expect(redeemed.status(), "the invitation redeems").toBe(200)
  return (await redeemed.json()).session_token as string
}
