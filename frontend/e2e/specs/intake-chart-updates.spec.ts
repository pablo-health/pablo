// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * An accepted intake form proposes chart updates, and the clinician decides them.
 *
 * The client fills a form in through the portal: their pronouns, who to call
 * in an emergency, and their earlier therapy, each question marked with the
 * chart field it fills. Handing it in changes nothing on the chart. The
 * clinician accepts the form; the chart then shows the three updates under
 * "From the intake on <date>", each with the question and the answer it came
 * from. Accepting one writes the chart, discarding one writes nothing, and
 * the question on an update opens the form at that answer.
 *
 * What no unit test reaches: the portal's answers arriving in the shape the
 * mapping reads, the accept button on the review being what proposes, and
 * the strip on the chart deciding through the form's own route.
 */

import { expect, test } from "../fixtures/auth"
import type { Page } from "@playwright/test"
import type { ApiClient } from "../fixtures/api"
import { givePortalInvitation, openPortalSection, signInToPortal } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"

interface IntakeTemplate {
  id: string
  versions: { id: string }[]
}

interface IntakeVersionDetail {
  id: string
  published_at: string | null
}

interface Assignment {
  id: string
  status: string
}

interface PatientRecord {
  pronouns: string | null
  emergency_contact: string | null
}

interface HistoryField {
  key: string
  text: string | null
}

const PRONOUNS_Q = "What pronouns do you use?"
const CONTACT_Q = "Who should we contact in an emergency?"
const THERAPY_Q = "Have you had therapy or counseling before?"
const THERAPY = "Saw a counselor in college for a year."

/** Publish a three-question form whose answers each name the chart field they fill. */
async function publishDetailsForm(api: ApiClient): Promise<string> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `About you ${Date.now().toString(36)}`,
  })
  const draftId = template.versions[0].id
  await api.put(`/api/intake/templates/${template.id}/versions/${draftId}/items`, {
    items: [
      {
        key: "pronouns",
        item_type: "free_text",
        label: PRONOUNS_Q,
        config: { max_len: 64, chart_field: "pronouns" },
      },
      { key: "emergency_contact", item_type: "emergency_contact", label: CONTACT_Q, config: {} },
      {
        key: "prior_treatment",
        item_type: "free_text",
        label: THERAPY_Q,
        config: { chart_field: "psychotherapy_history" },
      },
    ],
  })
  const published = await api.post<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
  expect(published.published_at, "a form whose questions name chart fields publishes").not.toBeNull()
  return published.id
}

/** Walk the form in the portal and hand it in. */
async function fillAndHandIn(page: Page): Promise<void> {
  await page.getByTestId("forms-list-open").click()

  await expect(page.getByRole("heading", { name: PRONOUNS_Q })).toBeVisible()
  await page.getByTestId("forms-free-text").fill("they/them")
  await page.getByTestId("forms-continue").click()

  await expect(page.getByRole("heading", { name: CONTACT_Q })).toBeVisible()
  await page.getByTestId("forms-contact-name").fill("Jordan Lee")
  await page.getByTestId("forms-contact-relationship").fill("sister")
  await page.getByTestId("forms-contact-phone").fill("555-0100")
  await page.getByTestId("forms-continue").click()

  await expect(page.getByRole("heading", { name: THERAPY_Q })).toBeVisible()
  await page.getByTestId("forms-free-text").fill(THERAPY)
  await page.getByTestId("forms-continue").click()

  await expect(page.getByTestId("forms-review")).toBeVisible()
  await page.getByTestId("forms-submit").click()
  await expect(page.getByTestId("forms-receipt-code")).toBeVisible()
}

async function historyText(api: ApiClient, patientId: string, key: string) {
  const chart = await api.get<{ groups: { fields: HistoryField[] }[] }>(
    `/api/patients/${patientId}/chart-history`,
  )
  return chart.groups.flatMap((g) => g.fields).find((f) => f.key === key)?.text ?? null
}

test("an accepted intake proposes chart updates the clinician decides", async ({ api, page }) => {
  const stamp = Date.now().toString(36)
  const email = `intake-chart-${stamp}@example.com`
  const phone = `+1555${`${Date.now()}`.slice(-7)}`

  const patient = await givePatient(api, { email, phone, date_of_birth: "1993-04-21" })
  const assigned = await api.post<Assignment>(`/api/patients/${patient.id}/intake-assignments`, {
    version_id: await publishDetailsForm(api),
  })

  await test.step("the client hands the form in, and the chart is unchanged", async () => {
    await signInToPortal(page, await givePortalInvitation(api, patient.id, email, phone))
    await openPortalSection(page, "forms")
    await expect(page.getByTestId("forms-list")).toBeVisible()
    await fillAndHandIn(page)

    const waiting = await api.get<{ pending: number }>(`/api/patients/${patient.id}/chart-proposals`)
    expect(waiting.pending, "handing in proposes nothing").toBe(0)
  })

  // The practice's page, beside the portal's, in the same context.
  const chartPage = await page.context().newPage()
  const strip = chartPage.getByRole("region", { name: "Chart updates waiting" })

  await test.step("accepting the form puts its updates on the chart, with their source", async () => {
    await chartPage.goto(`/dashboard/patients/${patient.id}?tab=intake`)
    await chartPage.getByTestId(`intake-assignment-open-${assigned.id}`).click()
    const review = chartPage.getByTestId("intake-review-panel")
    await expect(review.getByTestId("intake-review-status")).toContainText("Handed in.")
    await review.getByTestId("intake-review-accept").click()
    await expect(review.getByTestId("intake-review-status")).toContainText("Accepted.")
    await chartPage.keyboard.press("Escape")

    await expect(strip).toContainText("3 chart updates waiting from 1 intake")
    await expect(strip.getByTestId("chart-updates-intake-group")).toContainText(
      "From the intake on",
    )
    const contact = strip.getByRole("listitem", { name: "Contacts: Emergency contact" })
    await expect(contact).toContainText("Jordan Lee (sister), 555-0100")
    await expect(contact).toContainText(CONTACT_Q)
  })

  await test.step("the question on an update opens the form at that answer", async () => {
    await strip
      .getByRole("listitem", { name: "Psychiatric history: Psychotherapy" })
      .getByRole("button", { name: THERAPY_Q })
      .click()
    const dialog = chartPage.getByTestId("intake-review-dialog")
    await expect(dialog.locator("[data-focused]")).toContainText(THERAPY)
    await chartPage.keyboard.press("Escape")
  })

  await test.step("accepting one changes the chart; discarding one leaves it", async () => {
    await strip
      .getByRole("listitem", { name: "Name and pronouns: Pronouns" })
      .getByRole("button", { name: "Accept" })
      .click()
    await expect(chartPage.getByTestId("chart-name-and-pronouns")).toContainText("they/them")

    await strip
      .getByRole("listitem", { name: "Psychiatric history: Psychotherapy" })
      .getByRole("button", { name: "Accept" })
      .click()

    await strip
      .getByRole("listitem", { name: "Contacts: Emergency contact" })
      .getByRole("button", { name: "Discard" })
      .click()
    await expect(strip).toHaveCount(0)

    const record = await api.get<PatientRecord>(`/api/patients/${patient.id}`)
    expect(record.pronouns).toBe("they/them")
    expect(record.emergency_contact, "a discarded update writes nothing").toBeNull()
    await expect(chartPage.getByTestId("chart-emergency-contact")).toHaveCount(0)
    expect(await historyText(api, patient.id, "psychotherapy_history")).toBe(THERAPY)
  })
})
