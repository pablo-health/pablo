// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Choosing what Pablo does with a payer, rather than being given all of it.
 *
 * Enrolling for remittances moves that payer's ERAs here from wherever they
 * arrive today. A practice with a billing service downstream has someone
 * posting payments from those, so it is off until she says otherwise — and
 * the sentence saying what moves is on screen before she can say it.
 *
 * The round trip is the point: the switch is a column on the payer row, so
 * this reloads and reads it back rather than trusting the checkbox it just
 * clicked.
 */

import type { Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"

const PAYERS = "/dashboard/settings/insurance"

async function openPayer(page: Page, name: string) {
  await page.goto(PAYERS)
  await page.getByRole("button", { name: new RegExp(name, "i") }).click()
}

/** A payer of this run's own, so a previous run's choices are not this one's. */
async function addPayer(page: Page): Promise<string> {
  const name = `Choice Test ${Date.now().toString(36)}`
  await page.goto(PAYERS)
  await page.getByRole("button", { name: /add a payer/i }).click()
  await page.getByRole("button", { name: /isn.t listed/i }).click()
  await page.getByLabel("Name").fill(name)
  await page.getByLabel("Payer ID").fill("99999")
  await page.getByRole("button", { name: "Add" }).click()
  await expect(page.getByText(name)).toBeVisible()
  return name
}

test("a new payer is not enrolled for remittances, and says why", async ({
  signedInPage: page,
}) => {
  const name = await addPayer(page)
  await openPayer(page, name)

  await expect(page.getByLabel("Receive remittances (ERAs)")).not.toBeChecked()
  await expect(page.getByLabel("File claims")).toBeChecked()
  await expect(page.getByLabel("Check eligibility")).toBeChecked()
  await expect(page.getByText(/stop arriving wherever they arrive today/i)).toBeVisible()
})

test("turning one on is remembered", async ({ signedInPage: page }) => {
  const name = await addPayer(page)
  await openPayer(page, name)

  await page.getByLabel("Receive remittances (ERAs)").click()
  await expect(page.getByLabel("Receive remittances (ERAs)")).toBeChecked()

  // Read it back off the row, not off the click.
  await openPayer(page, name)
  await expect(page.getByLabel("Receive remittances (ERAs)")).toBeChecked()
})

test("a payer she has asked nothing of has nothing to enroll for", async ({
  signedInPage: page,
}) => {
  const name = await addPayer(page)
  await openPayer(page, name)

  // One at a time: each is its own save, and the list refetches after each.
  await page.getByLabel("File claims").click()
  await expect(page.getByLabel("File claims")).not.toBeChecked()
  await page.getByLabel("Check eligibility").click()
  await expect(page.getByLabel("Check eligibility")).not.toBeChecked()

  await expect(page.getByRole("button", { name: "Enroll with payer" })).toBeDisabled()
})
