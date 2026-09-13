// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the directory already knew, finally reaching the screen.
 *
 * Every payer-search hit carries an `enrollment` object saying what enrolling
 * will actually cost: how long the payer takes to answer, whether it needs
 * anything from her, whether it wants a PTAN, and — the consequential one —
 * whether enrolling moves every NPI under her tax id rather than just her own.
 * All of it was on the wire and none of it was modelled, so the product kept
 * asking a question it already had the answer to.
 *
 * The stack's fake clearinghouse serves the same recorded response the unit
 * tests use, so these assertions run against the real shape rather than a
 * hand-written double.
 */

import type { Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"

const PAYERS = "/dashboard/settings/insurance"

async function searchFor(page: Page, term: string) {
  await page.goto(PAYERS)
  await page.getByRole("button", { name: /add a payer/i }).click()
  await page.getByLabel(/find your insurer/i).fill(term)
  await page.getByRole("button", { name: "Search" }).click()
  return page.getByRole("list", { name: "Payer search results" })
}

test("a payer says how long it will keep her waiting", async ({ signedInPage: page }) => {
  const results = await searchFor(page, "stedi")

  // The recorded directory marks claimPayment INSTANT. Rendered in words she
  // can plan around rather than the vendor's enum — "straight away", not
  // "INSTANT" — because the audience is a therapist, not an integrator.
  await expect(results.getByText(/remittance takes straight away/i)).toBeVisible()
})

test("the row still leads with what the payer requires", async ({ signedInPage: page }) => {
  const results = await searchFor(page, "stedi")

  // The timeframe is additional context, not a replacement: what she needs
  // first is whether an enrollment is needed at all.
  await expect(results.getByText(/needs enrollment for remittance/i)).toBeVisible()
})

test("a payer that can be enrolled narrowly raises no alarm", async ({
  signedInPage: page,
}) => {
  const results = await searchFor(page, "stedi")

  // The fixture offers both NPI and TIN aggregation, so the narrow one can be
  // asked for and nobody else's routing moves. Warning here would be crying
  // wolf — and a warning that fires on every payer is one she stops reading.
  await expect(results.getByText(/everyone billing under your tax ID/i)).toHaveCount(0)
})
