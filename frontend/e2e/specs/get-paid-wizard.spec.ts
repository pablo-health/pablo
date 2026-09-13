// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Billing setup for a therapist who works through a platform, in a real
 * browser, from the first question to a finished state.
 *
 * The platform case is the general one, which is why it is the walkthrough
 * here rather than a variant tucked beside the others. It exercises everything
 * at once — cash-pay work outside the platform, optional credentialing, the
 * restraint the platform tick buys, and a genuine ending — and the therapist
 * who only wants to take cash is a strict subset of it.
 *
 * What only a real run can show, and the component tests cannot:
 *
 * - the answers actually PERSIST, through the preferences API and back, so
 *   closing the tab mid-setup really does cost her nothing
 * - the steps she walks are the ones the checklist implied, on the real shell
 *   inside the dashboard
 * - nothing on the way through asks a platform clinician for the things only
 *   an independent biller needs
 */

import { expect, test } from "../fixtures/auth"

const SETUP_PATH = "/dashboard/billing/setup"

const SELF_PAY = "Clients pay me themselves"
const PLATFORM = /Headway, Alma, Rula/
const OWN_INSURANCE = "Insurance I bill myself"

test("she can say she is on a platform AND takes clients directly", async ({
  signedInPage: page,
}) => {
  // The answer the single-select router had no way to accept. She had to pick
  // one, and the product decided the rest for her.
  await page.goto(SETUP_PATH)

  await expect(page.getByText("How do clients pay you today?")).toBeVisible()

  await page.getByLabel(PLATFORM).check()
  await page.getByLabel(SELF_PAY).check()

  await expect(page.getByLabel(PLATFORM)).toBeChecked()
  await expect(page.getByLabel(SELF_PAY)).toBeChecked()

  await page.screenshot({
    path: "e2e/test-results/get-paid-checklist.png",
    fullPage: true,
  })
})

test("the platform case walks end to end and finishes", async ({ signedInPage: page }) => {
  await page.goto(SETUP_PATH)

  await page.getByLabel(PLATFORM).check()
  await page.getByLabel(SELF_PAY).check()
  await page.getByRole("button", { name: "Continue" }).click()

  // Screen 2 is a confirmation, not a question — and it says plainly that her
  // existing arrangement is not being touched.
  await expect(page.getByText("Here's what we'll set up")).toBeVisible()
  await expect(page.getByTestId("platform-untouched")).toBeVisible()
  await expect(page.getByTestId("wants-credentialing")).not.toBeChecked()

  await page.getByRole("button", { name: "Set up billing" }).click()

  // The shared spine: every practice needs these, whoever pays her.
  await expect(page.getByText("What we found")).toBeVisible()

  for (let i = 0; i < 4; i++) {
    await page.getByRole("button", { name: "Continue" }).click()
  }

  // A real finish, not an empty list.
  await expect(page.getByText(/Set up alongside|alongside the service/i)).toBeVisible()

  await page.screenshot({
    path: "e2e/test-results/get-paid-platform-done.png",
    fullPage: true,
  })
})

test("a platform clinician is never asked for what only an independent biller needs", async ({
  signedInPage: page,
}) => {
  // The care flag, proven by absence. Her platform handles the insurance side,
  // so nothing in her setup should reach for payers or enrollment.
  await page.goto(SETUP_PATH)

  await page.getByLabel(PLATFORM).check()
  await page.getByLabel(SELF_PAY).check()
  await page.getByRole("button", { name: "Continue" }).click()
  await page.getByRole("button", { name: "Set up billing" }).click()

  for (let i = 0; i < 5; i++) {
    await expect(page.getByRole("heading", { name: "Payers" })).toBeHidden()
    const next = page.getByRole("button", { name: "Continue" })
    if (!(await next.isVisible())) break
    await next.click()
  }

  await expect(page.getByRole("heading", { name: "Payers" })).toBeHidden()
})

test("her answers survive closing the tab", async ({ signedInPage: page }) => {
  // The promise the wizard makes out loud. Nothing else in the flow is worth
  // much if this is not true.
  await page.goto(SETUP_PATH)

  await page.getByLabel(PLATFORM).check()
  await page.getByLabel(SELF_PAY).check()
  await page.getByRole("button", { name: "Continue" }).click()
  await expect(page.getByText("Here's what we'll set up")).toBeVisible()

  await page.reload()

  await expect(page.getByText("Here's what we'll set up")).toBeVisible()
  await expect(page.getByTestId("platform-untouched")).toBeVisible()
})

test("asking for credentialing adds the screens it needs, and only then", async ({
  signedInPage: page,
}) => {
  await page.goto(SETUP_PATH)

  await page.getByLabel(PLATFORM).check()
  await page.getByRole("button", { name: "Continue" }).click()

  await page.getByTestId("wants-credentialing").check()
  await page.getByRole("button", { name: "Set up billing" }).click()

  // Payers and the credentialing record appear now, because she asked — not
  // because she is on a platform.
  let sawPayers = false
  for (let i = 0; i < 8; i++) {
    if (await page.getByRole("heading", { name: "Payers" }).isVisible()) sawPayers = true
    const next = page.getByRole("button", { name: "Continue" })
    if (!(await next.isVisible())) break
    await next.click()
  }

  expect(sawPayers).toBe(true)
})

test("a therapist who only takes cash walks the same, shorter path", async ({
  signedInPage: page,
}) => {
  // The subset case. If the platform walkthrough works, this one falls out of
  // it — same spine, one fewer fact, no insurance screens at all.
  await page.goto(SETUP_PATH)

  await page.getByLabel(SELF_PAY).check()
  await page.getByRole("button", { name: "Continue" }).click()
  await expect(page.getByTestId("platform-untouched")).toBeHidden()

  await page.getByRole("button", { name: "Set up billing" }).click()

  await expect(page.getByRole("heading", { name: "Payers" })).toBeHidden()
})

test("not seeing clients yet is an answer, not a dead end", async ({ signedInPage: page }) => {
  await page.goto(SETUP_PATH)

  await expect(page.getByRole("button", { name: "Continue" })).toBeDisabled()

  await page.getByRole("button", { name: /not seeing clients yet/i }).click()

  await expect(page.getByText("Here's what we'll set up")).toBeVisible()
})

test("someone already billing insurance herself is asked about payers", async ({
  signedInPage: page,
}) => {
  await page.goto(SETUP_PATH)

  await page.getByLabel(OWN_INSURANCE).check()
  await page.getByRole("button", { name: "Continue" }).click()
  await page.getByRole("button", { name: "Set up billing" }).click()

  let sawPayers = false
  for (let i = 0; i < 8; i++) {
    if (await page.getByRole("heading", { name: "Payers" }).isVisible()) sawPayers = true
    const next = page.getByRole("button", { name: "Continue" })
    if (!(await next.isVisible())) break
    await next.click()
  }

  expect(sawPayers).toBe(true)
})
