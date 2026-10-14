// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice that takes no cards opens a client's chart.
 *
 * The chart reads the client's charge list on every open, to count the
 * Billing tab. That read used to answer 503 when no card processor was
 * configured, so every chart open in such a practice logged a server error.
 * The server-error guard deliberately lets 503 through, so nothing noticed —
 * which is why this spec watches the response itself.
 *
 * This stack runs with no card processor configured (docker-compose.e2e.yml),
 * so it is exactly that practice.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

test("opening a chart reads the charge list without a server error", async ({
  signedInPage: page,
  api,
}) => {
  const patient = await givePatient(api)

  const chargesRead = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === `/api/patients/${patient.id}/charges`,
  )
  await page.goto(`/dashboard/patients/${patient.id}`)

  const response = await chargesRead
  expect(response.status()).toBe(200)
  expect(await response.json()).toEqual([])
})

test("the payments section says cards are not set up, and nothing else", async ({
  signedInPage: page,
  api,
}) => {
  const patient = await givePatient(api)

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByRole("tab", { name: /Billing/ }).click()

  const payments = page.getByRole("region", { name: "Payments" })
  await expect(payments.getByText("Card payments are not set up for this practice.")).toBeVisible()
  await expect(page.getByRole("button", { name: /add a card/i })).toHaveCount(0)
  // An empty list under "not set up" would read as if charging were possible.
  await expect(payments.getByText("No charges yet.")).toHaveCount(0)
})

test("a payment recorded without cards shows in the charge history", async ({
  signedInPage: page,
  api,
}) => {
  const patient = await givePatient(api)

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByRole("tab", { name: /Billing/ }).click()
  await page.getByRole("button", { name: "Record payment" }).click()
  await page.getByLabel("Amount").fill("80.00")
  await page.getByRole("combobox", { name: /how it arrived/i }).click()
  await page.getByRole("option", { name: "Check" }).click()
  await page.getByLabel("Check number").fill("2210")
  await page.getByRole("button", { name: "Record payment" }).click()

  await expect
    .poll(async () => (await api.get<unknown[]>(`/api/patients/${patient.id}/charges`)).length)
    .toBe(1)

  const payments = page.getByRole("region", { name: "Payments" })
  await expect(payments.getByText("Card payments are not set up for this practice.")).toBeVisible()
  await expect(payments.getByText("$80.00", { exact: true })).toBeVisible()
})
