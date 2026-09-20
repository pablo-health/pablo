// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A form that asks for files, in a browser.
 *
 * `portal-forms.spec.ts` proves the walk for questions somebody types an
 * answer to. This is the half that asks for a file instead, and what a
 * browser is uniquely positioned to prove about it: that a card renders one
 * slot per side the practice asked for, that a document request always
 * offers one more, and — the part that matters most — that a form still
 * waiting for a file cannot be handed in, with the server naming exactly
 * which questions are outstanding.
 *
 * **The safety property is asserted through the API, not the browser.** The
 * answer to one of these questions names documents, so a patient who could
 * send that value through the ordinary save route would be attaching files
 * that may not be theirs. The browser has no way to try that — the renderer
 * never calls save for these types — so it is driven directly at the route,
 * which is where the refusal lives.
 *
 * **And the round trip, now that the stack has an object store.** A patient
 * chooses three files at three real pickers, each one goes to storage
 * directly against a signed URL, the form goes from two questions
 * outstanding to ready, and the practice reads back the same bytes. That
 * chain crosses a browser, an API, and a store, and no other layer's tests
 * can see all three at once: the route layer
 * (`backend/tests/test_patient_intake_artifacts_api.py`) and the database
 * layer (`backend/tests_integration/database/test_patient_intake_artifacts_rls.py`)
 * each see their own end of it.
 *
 * So the comparison is a SHA-256 rather than "a file appeared". An upload
 * that truncated, re-encoded, or attached the back of the card where the
 * front belonged would still produce three rows.
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
import { fixtureFile, sha256, toInputFile } from "../fixtures/upload"

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
  progress: { complete: boolean; missing: string[] }
  artifacts?: { id: string; item_id: string; side: string | null; document_id: string }[]
}

const CARD_QUESTION = "A photo of your insurance card"
const RECORDS_QUESTION = "Any records from a previous provider"

/**
 * Publish a form of the practice's own that asks for two files.
 *
 * Through the builder's API rather than the settings screens: what this
 * spec is about starts once a patient opens the form.
 */
async function publishFileForm(api: ApiClient): Promise<IntakeVersionDetail> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `Before we meet ${Date.now().toString(36)}`,
  })
  const draftId = template.versions[0].id

  await api.put<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/items`,
    {
      items: [
        {
          key: "card",
          item_type: "insurance_card",
          label: CARD_QUESTION,
          config: { sides: "both" },
        },
        {
          key: "records",
          item_type: "document_request",
          label: RECORDS_QUESTION,
          config: {},
        },
      ],
    },
  )

  const published = await api.post<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
  expect(published.published_at, "a form is only ever sent frozen").not.toBeNull()
  return published
}

/**
 * Invite a patient and sign them in through the shell, as the patient does.
 *
 * The token rides in the link's fragment (`/portal/{slug}#invite=…`), which
 * is what keeps it out of request logs and referrers on the way to the page.
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

test.describe("intake artifacts", () => {
  test("a form waiting for files renders them and refuses to be sent", async ({ api, page }) => {
    const suffix = Date.now().toString(36)
    const email = `artifacts-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1988-11-02" })
    const version = await publishFileForm(api)
    const cardItem = version.items.find((item) => item.item_type === "insurance_card")!
    const recordsItem = version.items.find((item) => item.item_type === "document_request")!

    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: version.id },
    )
    // Both questions are outstanding before anything is sent, and the
    // server is what says so.
    expect(assigned.progress.complete).toBe(false)
    expect(assigned.progress.missing).toEqual([cardItem.id, recordsItem.id])

    await signIn(api, page, patient.id, email, phone)

    await expect(page.getByTestId("forms-list")).toBeVisible()
    await expect(page.getByTestId("forms-list-state")).toContainText("2 questions left")
    await page.getByTestId("forms-list-open").click()

    // --- the card: one slot per side the practice asked for ---------------
    await expect(page.getByRole("heading", { name: CARD_QUESTION })).toBeVisible()
    await expect(page.getByTestId("forms-progress")).toContainText("Question 1 of 2")
    await expect(page.getByTestId("forms-upload-front")).toBeVisible()
    await expect(page.getByTestId("forms-upload-back")).toBeVisible()
    // Nothing has arrived, so both still ask.
    await expect(page.getByTestId("forms-upload-front-choose")).toBeVisible()
    await expect(page.getByTestId("forms-upload-back-choose")).toBeVisible()
    // The practice did not ask for the plan in words, so it is not asked.
    await expect(page.getByTestId("forms-coverage-fields")).toHaveCount(0)

    await page.getByTestId("forms-continue").click()

    // --- the document request: always one more slot -----------------------
    await expect(page.getByRole("heading", { name: RECORDS_QUESTION })).toBeVisible()
    await expect(page.getByTestId("forms-progress")).toContainText("Question 2 of 2")
    await expect(page.getByTestId("forms-upload-choose")).toHaveCount(1)
    // No blank form is named, so nothing offers one to print.
    await expect(page.getByTestId("forms-blank-form")).toHaveCount(0)

    await page.getByTestId("forms-continue").click()

    // --- and it cannot be sent ---------------------------------------------
    // Both questions appear on the review screen — they are questions, not
    // headings — and both read as unanswered.
    await expect(page.getByTestId("forms-review")).toBeVisible()
    await expect(page.getByTestId("forms-review")).toContainText(CARD_QUESTION)
    await expect(page.getByTestId("forms-review")).toContainText(RECORDS_QUESTION)

    await page.getByTestId("forms-submit").click()

    // The server refuses and the form stays open. Nothing in the browser
    // decided this: sending is offered whatever the answers look like, and
    // the refusal is the server's.
    await expect(page.getByTestId("forms-review")).toBeVisible()
    await expect(page.getByTestId("forms-receipt-code")).toHaveCount(0)

    const stillOpen = await api.get<Assignment>(
      `/api/patients/${patient.id}/intake-assignments/${assigned.id}`,
    )
    expect(stillOpen.status).not.toBe("submitted")
    expect(stillOpen.progress.missing).toEqual([cardItem.id, recordsItem.id])
    // The chart read carries the files that arrived, and none have.
    expect(stillOpen.artifacts).toEqual([])
  })

  test("a file-backed answer cannot be typed through the save route", async ({ api, page }) => {
    const suffix = Date.now().toString(36)
    const email = `forged-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1979-04-08" })
    const version = await publishFileForm(api)
    const cardItem = version.items.find((item) => item.item_type === "insurance_card")!

    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: version.id },
    )
    await signIn(api, page, patient.id, email, phone)

    // The session the shell stored, driven at the route directly: no screen
    // offers this, which is the point — the refusal has to hold against
    // something that is not a screen.
    const sessionToken = await page.evaluate(() => {
      const key = Object.keys(window.localStorage).find((name) =>
        name.startsWith("pablo-portal-session:"),
      )
      if (key === undefined) return null
      return (JSON.parse(window.localStorage.getItem(key) as string) as { sessionToken: string })
        .sessionToken
    })
    expect(sessionToken, "the shell stores its session per practice slug").toBeTruthy()

    const refused = await page.request.put(
      `${api.baseUrl}/api/patient/intake/assignments/${assigned.id}/items/${cardItem.id}`,
      {
        headers: { Authorization: `Bearer ${sessionToken as string}` },
        data: {
          value: {
            documents: [
              { document_id: "00000000-0000-4000-8000-0000000000ff", side: "front" },
            ],
          },
        },
      },
    )

    // 422: this question is answered by sending a file, not by naming one.
    expect(refused.status()).toBe(422)

    // And the form is exactly as unfinished as it was.
    const stillOpen = await api.get<Assignment>(
      `/api/patients/${patient.id}/intake-assignments/${assigned.id}`,
    )
    expect(stillOpen.progress.missing).toContain(cardItem.id)
  })

  test("the files a patient chooses reach the practice unchanged", async ({ api, page }) => {
    const suffix = Date.now().toString(36)
    const email = `round-trip-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const front = fixtureFile("insurance-card.png", "image/png")
    const back = fixtureFile("insurance-card-back.png", "image/png")
    const records = fixtureFile("records.pdf", "application/pdf")

    const patient = await givePatient(api, { email, phone, date_of_birth: "1990-06-21" })
    const version = await publishFileForm(api)
    const cardItem = version.items.find((item) => item.item_type === "insurance_card")!
    const recordsItem = version.items.find((item) => item.item_type === "document_request")!

    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: version.id },
    )
    await signIn(api, page, patient.id, email, phone)

    await expect(page.getByTestId("forms-list-state")).toContainText("2 questions left")
    await page.getByTestId("forms-list-open").click()

    // --- the card, one side at a time -------------------------------------
    // The input is the real one behind the button, so the three calls the
    // slot makes are the three a person's press makes: ask where to put it,
    // put it there, say which question it answers.
    await page.getByTestId("forms-upload-front-input").setInputFiles(toInputFile(front))
    await expect(page.getByTestId("forms-upload-front-sent")).toBeVisible()

    await page.getByTestId("forms-upload-back-input").setInputFiles(toInputFile(back))
    await expect(page.getByTestId("forms-upload-back-sent")).toBeVisible()

    await page.getByTestId("forms-continue").click()

    // --- the document request ---------------------------------------------
    await expect(page.getByRole("heading", { name: RECORDS_QUESTION })).toBeVisible()
    await page.getByTestId("forms-upload-input").setInputFiles(toInputFile(records))
    await expect(page.getByTestId("forms-upload-sent")).toBeVisible()

    await page.getByTestId("forms-continue").click()
    await expect(page.getByTestId("forms-review")).toBeVisible()

    // Completion flips, and the server is what says so — the same read that
    // named both questions as outstanding before anything was sent.
    const ready = await api.get<Assignment>(
      `/api/patients/${patient.id}/intake-assignments/${assigned.id}`,
    )
    expect(ready.progress.complete).toBe(true)
    expect(ready.progress.missing).toEqual([])

    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toHaveText(/^[2-9A-HJ-NP-TV-Z]{8}$/)

    // --- what the practice has ---------------------------------------------
    const submitted = await api.get<Assignment>(
      `/api/patients/${patient.id}/intake-assignments/${assigned.id}`,
    )
    expect(submitted.status).toBe("submitted")
    const artifacts = submitted.artifacts ?? []
    expect(artifacts).toHaveLength(3)

    const cardFront = artifacts.find((a) => a.item_id === cardItem.id && a.side === "front")!
    const cardBack = artifacts.find((a) => a.item_id === cardItem.id && a.side === "back")!
    const record = artifacts.find((a) => a.item_id === recordsItem.id)!
    expect(cardFront, "the front of the card is on the chart").toBeDefined()
    expect(cardBack, "and the back, separately").toBeDefined()
    expect(record.side, "a document request has no sides").toBeNull()

    // Both faces are compared, not just one: they are different bytes, so a
    // pair attached the wrong way round passes a count and fails a hash.
    for (const [artifact, sent] of [
      [cardFront, front],
      [cardBack, back],
      [record, records],
    ] as const) {
      const link = await api.get<{ url: string }>(`/api/documents/${artifact.document_id}/file`)
      const fetched = await page.request.get(link.url)
      expect(fetched.status(), `the practice can fetch ${sent.name}`).toBe(200)
      expect(sha256(await fetched.body()), `${sent.name} survived the round trip`).toBe(
        sha256(sent.body),
      )
    }
  })

  test("a file that is not the kind of file it claims is refused", async ({ api, page }) => {
    const suffix = Date.now().toString(36)
    const email = `wrong-type-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1983-02-17" })
    const version = await publishFileForm(api)
    const cardItem = version.items.find((item) => item.item_type === "insurance_card")!

    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: version.id },
    )
    await signIn(api, page, patient.id, email, phone)
    await page.getByTestId("forms-list-open").click()

    // Named .png and declared image/png, so the picker's filter, the signed
    // URL and the storage service all wave it through. What catches it is
    // the server reading the stored file's opening bytes.
    await page.getByTestId("forms-upload-front-input").setInputFiles({
      name: "card.png",
      mimeType: "image/png",
      buffer: Buffer.from("this is not a picture of anything"),
    })

    await expect(page.getByTestId("forms-upload-front-error")).toBeVisible()
    await expect(page.getByTestId("forms-upload-front-error")).not.toBeEmpty()
    // The slot goes back to asking, rather than showing a file as sent.
    await expect(page.getByTestId("forms-upload-front-choose")).toBeVisible()
    await expect(page.getByTestId("forms-upload-front-sent")).toHaveCount(0)

    // And nothing reached the chart: the question is as outstanding as it
    // was, and no file is attached to the form.
    const stillOpen = await api.get<Assignment>(
      `/api/patients/${patient.id}/intake-assignments/${assigned.id}`,
    )
    expect(stillOpen.progress.missing).toContain(cardItem.id)
    expect(stillOpen.artifacts).toEqual([])
  })
})
