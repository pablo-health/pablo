// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Walking the seeded intake form in a browser, as the patient does.
 *
 * Factored out of `intake-review.spec.ts` so a second spec that needs a
 * handed-in form drives the same screens rather than a second copy of them
 * — the copies are what drift when the form's markup moves.
 *
 * Signing in is not here: `portal.ts` already owns inviting a patient and
 * reading both factors back off the stand-ins their channels are wired to.
 */

import { expect } from "@playwright/test"
import type { Page } from "@playwright/test"
import type { ApiClient } from "./api"

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

/** How many questions the seeded form asks, as its progress line counts them. */
export const SEEDED_FORM_QUESTIONS = 4

/** The published version of the form a fresh practice is seeded with. */
export async function defaultIntakeVersion(api: ApiClient): Promise<string> {
  const templates = await api.get<IntakeTemplate[]>("/api/intake/templates")
  const intake = templates.find((t) => t.name === "Intake" && t.archived_at === null)
  if (intake === undefined) {
    throw new Error("no Intake form on this practice; every schema is seeded with one")
  }
  const published = intake.versions.filter((v) => v.published_at !== null)
  expect(published.length, "the seeded version ships published").toBeGreaterThan(0)
  return published[0].id
}

/** A complete measure answer: every item scored at the first anchor above zero. */
export function everyItemScoredOne(items: number): Record<string, number> {
  return Object.fromEntries(Array.from({ length: items }, (_, i) => [`${i + 1}`, 1]))
}

/** The seeded form's questions, as the progress line numbers them. */
export async function atQuestion(page: Page, index: number): Promise<void> {
  await expect(page.getByTestId("forms-progress")).toContainText(
    `Question ${index} of ${SEEDED_FORM_QUESTIONS}`,
  )
}

/**
 * Answer every item of the measure on screen with its first anchor.
 *
 * The caller has to have pinned which screen this is first. `count()` is a
 * snapshot rather than an assertion, so calling it straight after a
 * Continue reads the screen the patient is leaving — and the two measures
 * are different lengths, so a count taken on the PHQ-9 walks off the end of
 * the GAD-7.
 */
export async function answerMeasureOnScreen(page: Page): Promise<void> {
  const groups = page.locator("fieldset[data-testid^='forms-item-']")
  await expect(groups.first(), "a measure renders one group per item").toBeVisible()
  const count = await groups.count()
  for (let index = 0; index < count; index += 1) {
    await groups.nth(index).getByRole("radio").first().check()
  }
}

/** Walk the seeded form end to end and hand it in. */
export async function fillTheFormIn(page: Page, reason: string): Promise<void> {
  await expect(page.getByTestId("forms-list")).toBeVisible()
  await page.getByTestId("forms-list-open").click()

  await atQuestion(page, 1)
  await page.getByTestId("forms-identity-confirm").click()
  await page.getByTestId("forms-continue").click()

  await atQuestion(page, 2)
  await page.getByTestId("forms-reason").fill(reason)
  await page.getByTestId("forms-continue").click()

  await atQuestion(page, 3)
  await answerMeasureOnScreen(page)
  await page.getByTestId("forms-continue").click()

  await atQuestion(page, 4)
  await answerMeasureOnScreen(page)
  await page.getByTestId("forms-continue").click()

  await expect(page.getByTestId("forms-review")).toBeVisible()
  await page.getByTestId("forms-submit").click()
  await expect(page.getByTestId("forms-receipt-code")).toBeVisible()
  await page.getByTestId("forms-receipt-close").click()
}
