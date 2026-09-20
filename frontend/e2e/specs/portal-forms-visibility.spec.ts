// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A form that asks a question only of the people it applies to.
 *
 * The rule itself is proved twice over by unit tests, once in Python and
 * once in TypeScript, against one shared table of cases. What neither can
 * reach is the thing this spec is for: that the browser and the server
 * agree about it in a real request. A rule evaluated one way when the
 * portal decides what to draw and another way when the server decides what
 * the form still needs is a patient stuck on a form they cannot hand in,
 * and the two halves agreeing in their own test suites is exactly the
 * failure mode that would not show up.
 *
 * Three journeys, which is the whole shape of the feature:
 *
 * - **No.** The follow-up is never drawn, and the form goes in without it
 *   even though it is a required question.
 * - **Yes.** The follow-up is drawn, and the server refuses it blank.
 * - **Yes, then no.** The answer given while it was shown is not handed in,
 *   the receipt says so, and the chart does not hold it.
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

interface IntakeVersion {
  id: string
  published_at: string | null
}

interface IntakeTemplate {
  id: string
  versions: IntakeVersion[]
}

interface IntakeItem {
  id: string
  key: string
  label: string | null
  value: Record<string, unknown> | null
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
  items?: IntakeItem[]
}

const TRIGGER = "Do you drink alcohol or use any other substances?"
const FOLLOW_UP = "What, and roughly how often?"

/**
 * Publish a two-question form whose second question a "yes" opens.
 *
 * Built through the builder's own API rather than seeded, because the rule
 * has to have survived a publish: the server validates it there, and a
 * fixture written straight into the database would skip the check this
 * feature most depends on.
 */
async function publishBranchingForm(api: ApiClient): Promise<string> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `Substance use ${Date.now().toString(36)}`,
  })
  const draftId = template.versions[0].id

  await api.put<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/items`,
    {
      items: [
        { key: "substances", item_type: "yes_no", label: TRIGGER, config: {} },
        {
          key: "which",
          item_type: "free_text",
          label: FOLLOW_UP,
          config: {
            max_len: 500,
            visible_when: { item_key: "substances", op: "eq", value: true },
          },
        },
      ],
    },
  )

  const published = await api.post<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
  expect(published.published_at, "a rule that validates is a rule that publishes").not.toBeNull()
  return published.id
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

/** A patient with a mailbox and a phone, and the form already on their list. */
async function invited(
  api: ApiClient,
  page: Page,
  label: string,
): Promise<{ patientId: string; assignment: Assignment }> {
  const suffix = `${label}-${Date.now().toString(36)}`
  const email = `${suffix}@example.com`
  const phone = `+1555${`${Date.now()}`.slice(-7)}`

  const patient = await givePatient(api, { email, phone, date_of_birth: "1990-06-11" })
  const versionId = await publishBranchingForm(api)
  const assignment = await api.post<Assignment>(
    `/api/patients/${patient.id}/intake-assignments`,
    { version_id: versionId },
  )

  await signIn(api, page, patient.id, email, phone)
  await page.getByTestId("forms-list-open").click()
  await expect(page.getByRole("heading", { name: TRIGGER })).toBeVisible()
  return { patientId: patient.id, assignment }
}

/** What the clinician's chart holds for one question, by its key. */
async function onChart(
  api: ApiClient,
  patientId: string,
  assignmentId: string,
): Promise<{ status: string; answers: Record<string, Record<string, unknown> | null> }> {
  const detail = await api.get<Required<Assignment>>(
    `/api/patients/${patientId}/intake-assignments/${assignmentId}`,
  )
  return {
    status: detail.status,
    answers: Object.fromEntries(detail.items.map((item) => [item.key, item.value])),
  }
}

test.describe("portal forms: a question asked only of some people", () => {
  test("answering no never asks the follow-up, and the form still goes in", async ({
    api,
    page,
  }) => {
    const { patientId, assignment } = await invited(api, page, "no")

    // The server counts both questions outstanding before anything is said:
    // nobody has answered the one the other depends on.
    expect(assignment.progress.complete).toBe(false)

    await page.getByTestId("forms-yes-no").getByText("No", { exact: true }).click()
    await page.getByTestId("forms-continue").click()

    // Straight to the review screen. The follow-up is a required question
    // and it is not on the walk, because this patient is not asked it.
    await expect(page.getByTestId("forms-review")).toBeVisible()
    await expect(page.getByTestId("forms-review")).toContainText(TRIGGER)
    await expect(page.getByTestId("forms-review")).not.toContainText(FOLLOW_UP)

    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toHaveText(/^[2-9A-HJ-NP-TV-Z]{8}$/)
    // Nothing was left out, so the receipt has nothing to add.
    await expect(page.getByTestId("forms-receipt-notes")).toHaveCount(0)

    const chart = await onChart(api, patientId, assignment.id)
    expect(chart.status).toBe("submitted")
    expect(chart.answers.substances).toEqual({ yes: false })
    expect(chart.answers.which).toBeNull()
  })

  test("answering yes asks the follow-up, and will not take it blank", async ({ api, page }) => {
    const { patientId, assignment } = await invited(api, page, "yes")

    await page.getByTestId("forms-yes-no").getByText("Yes", { exact: true }).click()
    await page.getByTestId("forms-continue").click()

    await expect(page.getByRole("heading", { name: FOLLOW_UP })).toBeVisible()

    // Required, and the server is what says so: pressing on with an empty
    // box is refused where it is, rather than at the review screen.
    await page.getByTestId("forms-continue").click()
    await expect(page.getByTestId("forms-item-error")).toBeVisible()
    await expect(page.getByRole("heading", { name: FOLLOW_UP })).toBeVisible()

    await page.getByTestId("forms-free-text").fill("Wine, four or five nights a week.")
    await page.getByTestId("forms-continue").click()

    await expect(page.getByTestId("forms-review")).toContainText(FOLLOW_UP)
    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toHaveText(/^[2-9A-HJ-NP-TV-Z]{8}$/)

    const chart = await onChart(api, patientId, assignment.id)
    expect(chart.answers.which).toEqual({ text: "Wine, four or five nights a week." })
  })

  test("taking the yes back leaves the answer out, and the receipt says so", async ({
    api,
    page,
  }) => {
    const { patientId, assignment } = await invited(api, page, "changed")

    await page.getByTestId("forms-yes-no").getByText("Yes", { exact: true }).click()
    await page.getByTestId("forms-continue").click()

    await expect(page.getByRole("heading", { name: FOLLOW_UP })).toBeVisible()
    await page.getByTestId("forms-free-text").fill("Wine, four or five nights a week.")
    await page.getByTestId("forms-continue").click()
    await expect(page.getByTestId("forms-review")).toBeVisible()

    // Back to the first question, and the answer changes.
    await page.getByTestId("forms-review-edit").first().click()
    await expect(page.getByRole("heading", { name: TRIGGER })).toBeVisible()
    await page.getByTestId("forms-yes-no").getByText("No", { exact: true }).click()
    await page.getByTestId("forms-continue").click()

    await expect(page.getByTestId("forms-review")).toBeVisible()
    await expect(page.getByTestId("forms-review")).not.toContainText(FOLLOW_UP)
    await page.getByTestId("forms-submit").click()

    await expect(page.getByTestId("forms-receipt-code")).toHaveText(/^[2-9A-HJ-NP-TV-Z]{8}$/)
    await expect(page.getByTestId("forms-receipt-notes")).toContainText("stopped applying")

    // And the chart does not hold an answer to a question this patient was
    // not, in the end, asked.
    const chart = await onChart(api, patientId, assignment.id)
    expect(chart.status).toBe("submitted")
    expect(chart.answers.substances).toEqual({ yes: false })
    expect(chart.answers.which).toBeNull()
  })
})
