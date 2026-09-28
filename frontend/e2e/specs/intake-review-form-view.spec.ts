// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The clinician reads a handed-in form the way the patient filled it in.
 *
 * The patient walks the seeded form in the portal and answers each measure
 * item differently, so a mark in the right place on the chart cannot be a
 * coincidence of every item sharing one answer. The clinician then clicks the
 * form on the chart's intake card, and it opens over the chart the way the
 * patient filled it in: each question in the wording it was asked, drawn by
 * the portal's own renderer, the chosen answer marked and every other one
 * not, nothing on it that can change an answer, and no raw stored values.
 * The print button is there; what the browser does with it is the browser's.
 *
 * What only a browser proves here is the seam: the wording the chart shows
 * beside an answer is the wording the server sent with the review, and the
 * answer is the one the portal saved. A unit test renders both from fixtures.
 */

import type { Locator, Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { atQuestion, defaultIntakeVersion } from "../fixtures/intake"
import { firstLink, mail } from "../fixtures/mail"
import { openPortalSection, signInFromLink } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"

const REASON = "Trouble sleeping since the move."
const ANCHORS = ["Not at all", "Several days", "More than half the days", "Nearly every day"]

/** Item `n` of a measure is answered with anchor `(n - 1) % 4`. */
function anchorFor(itemIndex: number): string {
  return ANCHORS[itemIndex % ANCHORS.length]
}

/** Answer the measure on screen, one different anchor per item. */
async function answerVaried(page: Page): Promise<number> {
  const groups = page.locator("fieldset[data-testid^='forms-item-']")
  await expect(groups.first(), "a measure renders one group per item").toBeVisible()
  const count = await groups.count()
  for (let index = 0; index < count; index += 1) {
    await groups.nth(index).getByRole("radio", { name: anchorFor(index) }).check()
  }
  return count
}

/** Every item group of a measure on the chart, with the anchor it should show. */
async function expectMeasureMarked(panel: Locator, code: string, items: number): Promise<void> {
  for (let index = 0; index < items; index += 1) {
    const group = panel.getByTestId(`forms-item-${code}-${index + 1}`)
    const chosen = group.getByRole("radio", { name: anchorFor(index), exact: true })
    await expect(chosen, `${code} item ${index + 1} is marked`).toBeChecked()
    await expect(chosen).toBeDisabled()
    for (const anchor of ANCHORS.filter((a) => a !== anchorFor(index))) {
      await expect(group.getByRole("radio", { name: anchor, exact: true })).not.toBeChecked()
    }
  }
}

test.describe("intake review, read as the patient saw it", () => {
  test("a clinician sees each answer marked against the question it answered", async ({
    api,
    page,
  }) => {
    const suffix = Date.now().toString(36)
    const email = `formview-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1990-02-11" })
    const versionId = await defaultIntakeVersion(api)
    const assigned = await api.post<{ id: string }>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: versionId },
    )

    // --- the patient fills it in -------------------------------------------
    await api.post(`/api/patients/${patient.id}/portal-invite`)
    await signInFromLink(page, firstLink(await mail.waitFor(email)), phone)
    await openPortalSection(page, "forms")
    await expect(page.getByTestId("forms-list")).toBeVisible()
    await page.getByTestId("forms-list-open").click()

    await atQuestion(page, 1)
    await page.getByTestId("forms-identity-confirm").click()
    await page.getByTestId("forms-continue").click()

    await atQuestion(page, 2)
    await page.getByTestId("forms-reason").fill(REASON)
    await page.getByTestId("forms-continue").click()

    await atQuestion(page, 3)
    const phqItems = await answerVaried(page)
    await page.getByTestId("forms-continue").click()

    await atQuestion(page, 4)
    const gadItems = await answerVaried(page)
    await page.getByTestId("forms-continue").click()

    await expect(page.getByTestId("forms-review")).toBeVisible()
    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toBeVisible()

    // --- the clinician opens it on the chart --------------------------------
    const chartPage = await page.context().newPage()
    await chartPage.goto(`/dashboard/patients/${patient.id}?tab=intake`)
    await chartPage.getByTestId(`intake-assignment-open-${assigned.id}`).click()
    const dialog = chartPage.getByRole("dialog")
    await expect(dialog).toBeVisible()
    const panel = dialog.getByTestId("intake-review-panel")
    await expect(panel).toBeVisible()
    await expect(panel.getByTestId("intake-review-print")).toBeVisible()

    // The identity check, with the name it asked about and the answer given.
    await expect(panel).toContainText("Is this you?")
    await expect(panel.getByTestId("forms-identity-confirm")).toHaveAttribute("aria-pressed", "true")
    await expect(panel.getByTestId("forms-identity-deny")).toHaveAttribute("aria-pressed", "false")
    await expect(panel.getByTestId("forms-identity-confirm")).toBeDisabled()

    // The reason, under the prompt it answered.
    await expect(panel).toContainText("What brings you in?")
    await expect(panel.getByTestId("forms-reason")).toContainText(REASON)

    // Each measure item in its own words, the chosen anchor marked.
    await expect(
      panel.getByRole("group", { name: "Feeling down, depressed, or hopeless" }),
    ).toBeVisible()
    await expect(panel.getByRole("group", { name: "Trouble relaxing" })).toBeVisible()
    await expectMeasureMarked(panel, "phq9", phqItems)
    await expectMeasureMarked(panel, "gad7", gadItems)

    // Read-only, and never the stored shape.
    await expect(panel.getByRole("radio").and(panel.locator(":enabled"))).toHaveCount(0)
    await expect(panel).not.toContainText("item_scores")
    await expect(panel).not.toContainText("name_confirmed")

    // The actions that were there before are still there.
    await expect(panel.getByTestId("intake-review-corrections")).toBeVisible()
    await expect(panel.getByTestId("intake-review-accept")).toBeVisible()

    await chartPage.close()
  })
})
