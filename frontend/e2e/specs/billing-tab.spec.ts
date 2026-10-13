// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The chart's Billing tab: balance, payments and insurance as three sections
 * of one tab, where they used to be three tabs.
 *
 * This stack runs with no card processing configured (docker-compose.e2e.yml),
 * which is the practice most likely to open Billing only for insurance — so
 * the first test is that practice reading the tab and finding no error in it.
 *
 * Links saved before the change still name the old tabs (`?tab=insurance`).
 * Those land on Billing, scrolled to the section they named. The viewport is
 * kept short so the lower sections start below the fold; at full height the
 * whole tab fits on screen and "scrolled to" would prove nothing.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

test("a practice without card processing reads Billing with no errors in it", async ({
  signedInPage: page,
  api,
}) => {
  const patient = await givePatient(api)

  await page.goto(`/dashboard/patients/${patient.id}`)
  for (const gone of [/^Payments/, /^Balance/, /^Insurance/]) {
    await expect(page.getByRole("tab", { name: gone })).toHaveCount(0)
  }
  await page.getByRole("tab", { name: /Billing/ }).click()

  const billing = page.getByRole("tabpanel")
  await expect(billing.getByRole("region", { name: "Balance" })).toBeVisible()
  await expect(billing.getByRole("button", { name: "Record payment" })).toBeVisible()

  const payments = billing.getByRole("region", { name: "Payments" })
  await expect(payments.getByText("Card payments are not set up for this practice.")).toBeVisible()

  const insurance = billing.getByRole("region", { name: "Insurance" })
  await expect(insurance.getByText(/^No insurance on file for this /)).toBeVisible()
  await expect(insurance.getByRole("button", { name: "Add coverage" })).toBeVisible()

  // Every section has finished loading by now; none of them should have
  // landed on its error state.
  await expect(billing.getByText(/^Failed to load/)).toHaveCount(0)
  await expect(billing.getByText(/not configured/i)).toHaveCount(0)
})

for (const section of ["payments", "insurance"] as const) {
  const heading = section === "payments" ? "Payments" : "Insurance"

  test(`a link to the old ${section} tab opens Billing at ${heading}`, async ({
    signedInPage: page,
    api,
  }) => {
    const patient = await givePatient(api)
    await page.setViewportSize({ width: 1280, height: 560 })

    await page.goto(`/dashboard/patients/${patient.id}?tab=${section}`)

    await expect(page.getByRole("tab", { name: /Billing/ })).toHaveAttribute(
      "aria-selected",
      "true",
    )
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeInViewport()
  })
}
