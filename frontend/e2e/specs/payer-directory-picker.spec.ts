// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Adding a carrier by finding it, rather than by knowing its code.
 *
 * `60054` is Aetna. Nobody knows that from memory, so the id used to be looked
 * up somewhere else or typed wrong — and a wrong one was only discovered when
 * an enrollment was filed against a payer that does not exist.
 *
 * What the directory answers with is the point: it says, per payer, which
 * transactions need an enrollment. Told here it is information; told after she
 * commits it is a surprise.
 *
 * Everything below scopes to the search results list. The practice's own payer
 * list is on the same screen and a payer that has been added appears in both,
 * so an unscoped match would be ambiguous — and the stack's database persists
 * between runs, so "already added" is a state these tests meet routinely.
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

test("the directory says what each payer will require, before she commits", async ({
  signedInPage: page,
}) => {
  const results = await searchFor(page, "stedi")

  // Two in the fixture directory, and they need different things — which is
  // exactly the fact she cannot get from a code she typed herself.
  await expect(results.getByText("Stedi Test Payer")).toBeVisible()
  await expect(results.getByText(/needs enrollment for remittance/i)).toBeVisible()
  await expect(results.getByText(/needs enrollment for eligibility/i)).toBeVisible()
})

test("picking one stores the directory's own name and code", async ({ signedInPage: page }) => {
  const results = await searchFor(page, "stedi")
  const row = results.getByRole("listitem").filter({ hasText: "Stedi Test Payer" })

  // A previous run may have added it already — the end state is what matters,
  // not who added it.
  const add = row.getByRole("button", { name: /^Add$/ })
  if (await add.isVisible()) await add.click()

  // Stored under the directory's name and id, so her label and the code cannot
  // disagree — which is what let two practices name one insurer differently.
  await expect(page.getByText(/Payer ID STEDI/).first()).toBeVisible()
})

test("a payer the directory does not have can still be added by hand", async ({
  signedInPage: page,
}) => {
  // The directory is authoritative about what it knows, not about what exists.
  await page.goto(PAYERS)
  await page.getByRole("button", { name: /add a payer/i }).click()
  await page.getByRole("button", { name: /isn.t listed/i }).click()

  const stamp = Date.now().toString(36)
  await page.getByLabel("Name").fill(`Hand Typed ${stamp}`)
  await page.getByLabel("Payer ID").fill("99999")
  await page.getByRole("button", { name: "Add" }).click()

  await expect(page.getByText(`Hand Typed ${stamp}`)).toBeVisible()
})
