// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The AI-tools consent, from the template a practice picks to the chart.
 *
 * A practice opens a draft form and adds "Consent for the use of AI tools"
 * from the template list — two clicks — and publishes it. Two clients then
 * meet it in the portal: each reads the document, signs it, picks one of the
 * two transcription answers, reloads to see the answer is still theirs, and
 * hands the form in. The chart header for each then says what they chose.
 *
 * What only a browser can show is the choice itself: a consent form whose
 * options cannot be ticked is the regression this guards, so both options
 * are picked on screen rather than written through the API.
 *
 * Between the two clients the practice changes how long session audio is
 * kept, and the second client's document says the new number: the period is
 * the practice's setting, read when the document is shown.
 */

import type { Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { BROWSER_TIME_ZONE } from "../fixtures/clock"
import { firstLink, mail } from "../fixtures/mail"
import { openPortalSection, signInFromLink } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BASE_URL } from "../fixtures/stack"

const TEMPLATE = "Consent for the use of AI tools"
const RETENTION = "/api/users/me/practice/audio-retention"

interface IntakeTemplate {
  id: string
  name: string
  versions: { id: string; published_at: string | null }[]
}

interface IntakeVersionDetail {
  id: string
  published_at: string | null
  items: { id: string; key: string; item_type: string }[]
}

interface AiConsentRecord {
  current: { decision: string; effective_on: string; source: string } | null
}

/** A date-only string as the chart header shows it. */
function shown(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number)
  return new Date(year, month - 1, day).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

/** The form's own row on the Forms card. */
function formRow(page: Page, name: string) {
  return page
    .getByRole("listitem")
    .filter({ has: page.getByRole("button", { name, exact: true }) })
}

/** Sign a client in from their invitation and open the form. */
async function openTheForm(
  api: ApiClient,
  page: Page,
  patientId: string,
  email: string,
  phone: string,
): Promise<void> {
  await api.post(`/api/patients/${patientId}/portal-invite`)
  await signInFromLink(page, firstLink(await mail.waitFor(email)), phone)
  await openPortalSection(page, "forms")
  await page.getByTestId("forms-list-open").click()
}

/** Read and sign the document, pick an answer, reload, and hand it in. */
async function answerAndHandIn(page: Page, signer: string, answer: string, days: number) {
  // The practice's retention period, as the setting reads now.
  await expect(page.getByTestId("forms-consent-document")).toContainText(TEMPLATE)
  await expect(page.getByTestId("forms-consent-document")).toContainText(
    `Your practice keeps session audio for ${days} days`,
  )
  await page.getByTestId("forms-consent-affirm").check()
  await page.getByTestId("forms-consent-name").fill(signer)
  await page.getByTestId("forms-consent-sign").click()
  await expect(page.getByTestId("forms-consent-signed")).toContainText(signer)
  await page.getByTestId("forms-continue").click()

  // The choice. Picked on screen, and checked once picked.
  const choice = page.getByTestId("forms-single-choice")
  await expect(choice).toBeVisible()
  await choice.getByLabel(answer, { exact: true }).check()
  await expect(choice.getByLabel(answer, { exact: true })).toBeChecked()
  await page.getByTestId("forms-continue").click()
  await expect(page.getByTestId("forms-review")).toBeVisible()

  // Reloaded: nothing kept in this browser, so the answer on the review is
  // the server's.
  await page.reload()
  await expect(page.getByTestId("forms-list-state")).toContainText("Ready to send")
  await page.getByTestId("forms-list-open").click()
  const review = page.getByTestId("forms-review")
  await expect(review).toContainText("Session transcription")
  await expect(review).toContainText(answer)
  await expect(review).toContainText("Signed")

  await page.getByTestId("forms-submit").click()
  await expect(page.getByTestId("forms-receipt-code")).toHaveText(/^[2-9A-HJ-NP-TV-Z]{8}$/)
}

test.describe("AI-tools consent", () => {
  let originalDays: number | null = null

  test.afterEach(async ({ api }) => {
    // The retention period is the practice's, and other specs share it.
    if (originalDays !== null) {
      await api.put(RETENTION, { audio_retention_days: originalDays })
    }
  })

  test("a practice adds it from a template and each client's answer reaches the chart @portal", async ({
    api,
    signedInPage: page,
    browser,
  }) => {
    const suffix = Date.now().toString(36)
    originalDays = (await api.get<{ audio_retention_days: number }>(RETENTION))
      .audio_retention_days
    await api.put(RETENTION, { audio_retention_days: 120 })

    // --- the practice: a draft form, the template, publish ------------------
    const formName = `AI tools ${suffix}`
    const template = await api.post<IntakeTemplate>("/api/intake/templates", { name: formName })

    await page.goto("/dashboard/settings/portal")
    const row = formRow(page, formName)
    await row.getByRole("button", { name: formName, exact: true }).click()

    await row.getByRole("button", { name: "Start from a template" }).click()
    await row.getByRole("button", { name: TEMPLATE }).click()

    // Added and saved: the question is on the form, and the practice's copy
    // of the document is in its documents list to edit like any other.
    await expect(row.getByText("Session transcription")).toBeVisible()
    await expect(page.getByRole("button", { name: TEMPLATE }).first()).toBeVisible()

    const versionPath = `/api/intake/templates/${template.id}/versions/${template.versions[0].id}`
    const readVersion = () => api.get<IntakeVersionDetail>(versionPath)
    await expect
      .poll(async () => (await readVersion()).items.map((i) => [i.key, i.item_type]))
      .toEqual([
        ["ai_tools_consent", "consent_document"],
        ["ai_transcription", "single_choice"],
      ])

    await row.getByRole("button", { name: "Publish" }).click()
    await expect.poll(async () => (await readVersion()).published_at !== null).toBe(true)
    const published = await readVersion()

    // --- two clients, one answer each ---------------------------------------
    const clients = [
      { answer: "I consent", decision: "consented", word: "agreed", days: 120 },
      { answer: "I do not consent", decision: "declined", word: "declined", days: 45 },
    ]
    for (const [index, client] of clients.entries()) {
      if (client.days !== 120) {
        // Changed between the two clients: the next document says so.
        await api.put(RETENTION, { audio_retention_days: client.days })
      }
      const email = `ai-consent-${index}-${suffix}@example.com`
      const phone = `+1555${`${Date.now()}`.slice(-7)}`
      const patient = await givePatient(api, { email, phone, date_of_birth: "1987-05-21" })
      await api.post(`/api/patients/${patient.id}/intake-assignments`, {
        version_id: published.id,
      })

      // A browser of the client's own, beside the clinician's.
      const context = await browser.newContext({
        baseURL: BASE_URL,
        timezoneId: BROWSER_TIME_ZONE,
      })
      const portal = await context.newPage()
      try {
        await openTheForm(api, portal, patient.id, email, phone)
        await answerAndHandIn(portal, `Client ${index} ${suffix}`, client.answer, client.days)
      } finally {
        await context.close()
      }

      // The answer is on the client's AI-notes record, from the form...
      const record = await api.get<AiConsentRecord>(`/api/patients/${patient.id}/ai-consent`)
      expect(record.current?.decision).toBe(client.decision)
      expect(record.current?.source).toBe("intake_form")

      // ...and the chart header says so.
      await page.goto(`/dashboard/patients/${patient.id}`)
      await expect(page.getByTestId("ai-consent-line")).toHaveText(
        `AI notes: ${client.word} ${shown(record.current?.effective_on ?? "")}`,
      )
    }
  })
})
