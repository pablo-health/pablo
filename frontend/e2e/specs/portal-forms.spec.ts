// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A patient fills the practice's form in from the portal, in a browser.
 *
 * `intake-submission.spec.ts` proves the same journey through the API and
 * says why: when this spec landed, there were no screens to click. There are
 * now, and this is the half a browser is uniquely positioned to prove —
 * that the questions render from what the server sent, that Continue saves
 * and advances, that closing the tab halfway loses nothing, and that the
 * receipt is what the patient is left holding. The API spec keeps the parts
 * a browser cannot reach: principal separation, the chart the clinician
 * reads afterwards, and the refusal to mint a second receipt.
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

interface IntakeItem {
  key: string
  label: string | null
  help_text: string | null
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
 * Build and publish a form of the practice's own, through the builder's API.
 *
 * Two questions the engine has no wording for: a written answer and a set of
 * choices. Which is the point — the seeded form asks only questions Pablo
 * words itself, so it cannot show that a question a practice typed reaches
 * the patient in the words they typed.
 */
async function publishAuthoredForm(api: ApiClient): Promise<string> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `Sleep and mood ${Date.now().toString(36)}`,
  })
  const draftId = template.versions[0].id

  const saved = await api.put<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/items`,
    {
      items: [
        {
          key: "sleep",
          item_type: "free_text",
          label: SLEEP_QUESTION,
          help_text: SLEEP_HELP,
          config: { max_len: 500 },
        },
        {
          key: "mornings",
          item_type: "single_choice",
          label: MORNINGS_QUESTION,
          config: {
            options: [
              { key: "easy", label: "Easily" },
              { key: "hard", label: "With difficulty" },
            ],
          },
        },
      ],
    },
  )
  expect(saved.items.map((item) => item.label)).toEqual([SLEEP_QUESTION, MORNINGS_QUESTION])

  const published = await api.post<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
  expect(published.published_at).not.toBeNull()
  return published.id
}

const SLEEP_QUESTION = "How have you been sleeping lately?"
const SLEEP_HELP = "A sentence or two is plenty."
const MORNINGS_QUESTION = "How do you get going in the mornings?"

const CONTACT_QUESTION = "Who should we call in an emergency?"
const CONTACT_HELP = "Someone we can reach if we can't reach you."
const CONTACT = { name: "Ada Lovelace", relationship: "Sister", phone: "555 0123" }

/**
 * Publish a form whose only question is the standard contact block.
 *
 * On its own rather than added to the authored form above, so the counts
 * and the review assertions there keep saying what they were written to
 * say. The practice writes the question, which is why one is passed: the
 * block is three fixed fields under a sentence they chose.
 */
async function publishContactForm(api: ApiClient): Promise<string> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `Emergency contact ${Date.now().toString(36)}`,
  })
  const draftId = template.versions[0].id

  await api.put<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/items`,
    {
      items: [
        {
          key: "contact",
          item_type: "emergency_contact",
          label: CONTACT_QUESTION,
          help_text: CONTACT_HELP,
        },
      ],
    },
  )

  const published = await api.post<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
  expect(published.published_at).not.toBeNull()
  return published.id
}

/**
 * Invite a patient and sign them in through the shell, as the patient does:
 * open the link the email carried, type the code the text carried.
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

/** Answer every item of the measure on screen with its first anchor. */
async function answerMeasureOnScreen(page: Page): Promise<void> {
  const groups = page.locator("fieldset[data-testid^='forms-item-']")
  const count = await groups.count()
  expect(count, "a measure renders one group per item").toBeGreaterThan(0)
  for (let index = 0; index < count; index += 1) {
    await groups.nth(index).getByRole("radio").first().check()
  }
}

test.describe("portal forms", () => {
  test("a patient fills the practice's form in and is given a receipt", async ({ api, page }) => {
    const suffix = Date.now().toString(36)
    const email = `forms-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1990-03-14" })
    const versionId = await defaultIntakeVersion(api)

    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: versionId },
    )
    expect(assigned.progress.complete).toBe(false)

    await signIn(api, page, patient.id, email, phone)

    // The form is on the list, with what the server says is outstanding.
    await expect(page.getByTestId("forms-list")).toBeVisible()
    await expect(page.getByTestId("forms-list-state")).toContainText("4 questions left")
    await page.getByTestId("forms-list-open").click()

    // Who you are: the chart's own name, for the patient to confirm.
    await expect(page.getByTestId("forms-identity-name")).toContainText(patient.first_name)
    await page.getByTestId("forms-identity-confirm").click()
    await page.getByTestId("forms-continue").click()

    // What brings you in.
    await expect(page.getByTestId("forms-reason")).toBeVisible()
    await page.getByTestId("forms-reason").fill("Panic before every shift, for about two months.")
    await page.getByTestId("forms-continue").click()

    // The first measure, and the crisis line that rides every one of them.
    await expect(page.getByTestId("forms-crisis-footer")).toContainText("988")
    await answerMeasureOnScreen(page)

    // --- halfway, the tab is reloaded ---------------------------------------
    // Nothing was kept in this browser, so where it comes back is the
    // server's answer: the first question still outstanding, which is the
    // measure just answered only because it has not been sent yet.
    await page.reload()
    await expect(page.getByTestId("forms-list")).toBeVisible()
    await expect(page.getByTestId("forms-list-state")).toContainText("2 questions left")
    await page.getByTestId("forms-list-open").click()
    await expect(page.getByTestId("forms-progress")).toContainText("Question 3 of 4")

    await answerMeasureOnScreen(page)
    await page.getByTestId("forms-continue").click()

    // The second measure.
    await expect(page.getByTestId("forms-progress")).toContainText("Question 4 of 4")
    await answerMeasureOnScreen(page)
    await page.getByTestId("forms-continue").click()

    // --- check, then send ---------------------------------------------------
    await expect(page.getByTestId("forms-review")).toBeVisible()
    await expect(page.getByTestId("forms-review")).toContainText("Panic before every shift")
    await page.getByTestId("forms-submit").click()

    // Eight characters the patient can read down a phone line.
    await expect(page.getByTestId("forms-receipt-code")).toHaveText(/^[2-9A-HJ-NP-TV-Z]{8}$/)
    await expect(page.getByTestId("forms-crisis-footer")).toContainText("988")
    // No total and no band reaches the person who answered the questions.
    // Matched lower case on purpose: the receipt code is upper case, so a
    // band word cannot be read out of a random eight characters.
    for (const band of ["minimal", "mild", "moderate", "severe"]) {
      await expect(page.getByTestId("forms-receipt")).not.toContainText(band)
    }

    // --- and the clinician has it -------------------------------------------
    const onChart = await api.get<Assignment>(
      `/api/patients/${patient.id}/intake-assignments/${assigned.id}`,
    )
    expect(onChart.status).toBe("submitted")
    expect(onChart.receipt_code).toMatch(/^[2-9A-HJ-NP-TV-Z]{8}$/)
  })

  test("a patient answers questions the practice wrote itself", async ({ api, page }) => {
    const suffix = Date.now().toString(36)
    const email = `authored-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1985-07-21" })
    const versionId = await publishAuthoredForm(api)

    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: versionId },
    )

    await signIn(api, page, patient.id, email, phone)
    await expect(page.getByTestId("forms-list-state")).toContainText("2 questions left")
    await page.getByTestId("forms-list-open").click()

    // The question is the practice's own sentence, not a heading this
    // browser invented, and the help text is under it.
    await expect(page.getByRole("heading", { name: SLEEP_QUESTION })).toBeVisible()
    await expect(page.getByTestId("forms-question-help")).toHaveText(SLEEP_HELP)
    await page.getByTestId("forms-free-text").fill("Waking around four most nights.")
    await page.getByTestId("forms-continue").click()

    await expect(page.getByRole("heading", { name: MORNINGS_QUESTION })).toBeVisible()
    await page
      .getByTestId("forms-single-choice")
      .getByRole("radio", { name: "With difficulty" })
      .check()
    await page.getByTestId("forms-continue").click()

    // The review screen names each question the way it was asked, and
    // repeats the answer rather than interpreting it.
    await expect(page.getByTestId("forms-review")).toContainText(SLEEP_QUESTION)
    await expect(page.getByTestId("forms-review")).toContainText("Waking around four most nights.")
    await expect(page.getByTestId("forms-review")).toContainText("With difficulty")
    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toHaveText(/^[2-9A-HJ-NP-TV-Z]{8}$/)

    // What the chart holds is the answer the patient gave, under the
    // question the practice wrote.
    const onChart = await api.get<{ status: string; items: { key: string; label: string | null }[] }>(
      `/api/patients/${patient.id}/intake-assignments/${assigned.id}`,
    )
    expect(onChart.status).toBe("submitted")
    expect(onChart.items.map((item) => item.label)).toEqual([SLEEP_QUESTION, MORNINGS_QUESTION])
  })

  test("a patient gives the practice someone to call", async ({ api, page }) => {
    const suffix = Date.now().toString(36)
    const email = `contact-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1979-11-05" })
    const versionId = await publishContactForm(api)

    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: versionId },
    )

    await signIn(api, page, patient.id, email, phone)
    await expect(page.getByTestId("forms-list-state")).toContainText("1 question left")
    await page.getByTestId("forms-list-open").click()

    // The practice's own sentence above three fixed fields.
    await expect(page.getByRole("heading", { name: CONTACT_QUESTION })).toBeVisible()
    await expect(page.getByTestId("forms-question-help")).toHaveText(CONTACT_HELP)

    // A name and no number is a question still to finish, and which field
    // is missing is the server's answer rather than this browser's.
    await page.getByTestId("forms-contact-name").fill(CONTACT.name)
    await page.getByTestId("forms-continue").click()
    await expect(page.getByTestId("forms-item-error")).toContainText(
      "Their phone number is still blank.",
    )

    await page.getByTestId("forms-contact-relationship").fill(CONTACT.relationship)
    await page.getByTestId("forms-contact-phone").fill(CONTACT.phone)
    await page.getByTestId("forms-continue").click()

    // The review screen reads the contact back, so it can be checked.
    await expect(page.getByTestId("forms-review")).toContainText(CONTACT_QUESTION)
    await expect(page.getByTestId("forms-review")).toContainText(
      `${CONTACT.name} · ${CONTACT.relationship} · ${CONTACT.phone}`,
    )
    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toHaveText(/^[2-9A-HJ-NP-TV-Z]{8}$/)

    // What the chart holds is the block, under the three keys the save
    // route stores it by.
    const onChart = await api.get<{
      status: string
      items: { key: string; value: Record<string, unknown> | null }[]
    }>(`/api/patients/${patient.id}/intake-assignments/${assigned.id}`)
    expect(onChart.status).toBe("submitted")
    expect(onChart.items[0].value).toEqual(CONTACT)
  })
})
