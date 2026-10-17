// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A client asks what they owe, and the practice hands them a statement.
 *
 * The statement's unit tests build it from an in-memory ledger. What they
 * cannot reach is the button on the chart's Balance tab, the route reading the
 * ledger back out of Postgres, and a real PDF arriving in the browser. A
 * button wired to the wrong path, or a payment the route never sees, is
 * invisible to them.
 *
 * The payment is recorded through the chart rather than seeded: this stack
 * has no card processor, and a cheque the practice already took is the one
 * kind of money that needs none. The PDF is written uncompressed, so its text
 * can be read straight off the bytes.
 */

import { readFile } from "node:fs/promises"

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

test("the Statement button downloads the client's statement, with what they have paid", async ({
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
  await page.getByLabel("Check number").fill("3301")
  await page.getByRole("button", { name: "Record payment" }).click()
  await expect
    .poll(async () => (await api.get<unknown[]>(`/api/patients/${patient.id}/charges`)).length)
    .toBe(1)

  const download = page.waitForEvent("download")
  await page.getByRole("button", { name: "Statement", exact: true }).click()
  const file = await download
  expect(file.suggestedFilename()).toBe("statement.pdf")

  const statement = (await readFile(await file.path())).toString("latin1")
  expect(statement.startsWith("%PDF")).toBe(true)
  expect(statement).toContain(`Account summary for ${patient.first_name} ${patient.last_name}`)
  // Paid ahead of any visit: money the practice holds, said as a credit.
  expect(statement).toContain("Credit balance: $80.00")
  await expect(page.getByText("The statement could not be generated.")).toHaveCount(0)
})
