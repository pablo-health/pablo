// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The first screen of billing setup, in a real browser.
 *
 * The component tests cover the branching. What only a real run shows is that
 * the screen mounts on the shared setup chrome inside the dashboard, with the
 * stepper and the illustration the shell provides, for a signed-in clinician.
 */

import { test, expect } from "../fixtures/auth"

const SETUP_PATH = "/dashboard/credentialing"

test("the first question renders on the setup shell", async ({ signedInPage: page }) => {
  await page.goto(SETUP_PATH)

  await expect(page.getByText("How do you get paid today?")).toBeVisible()
  await expect(page.getByText("My clients pay me directly")).toBeVisible()
  await expect(
    page.getByText("I want to accept insurance, but I’m not on a panel yet"),
  ).toBeVisible()

  await page.screenshot({
    path: "e2e/test-results/get-paid-screen-1.png",
    fullPage: true,
  })
})

test("choosing an answer moves on, and back returns with it still chosen", async ({
  signedInPage: page,
}) => {
  await page.goto(SETUP_PATH)

  await page.getByText("I’m already on insurance panels").click()
  await expect(page.getByText("How do you get paid today?")).toBeHidden()

  await page.getByRole("button", { name: "Back" }).click()

  await expect(page.getByText("How do you get paid today?")).toBeVisible()
  await expect(page.getByRole("button", { pressed: true })).toContainText(
    "I’m already on insurance panels",
  )
})
