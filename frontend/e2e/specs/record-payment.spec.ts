// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A client pays by cheque, and the practice can say so.
 *
 * This is the only test in the tree that proves the whole path against a real
 * database. The route's unit tests drive an in-memory ledger, so they cannot
 * reach the three CHECK constraints the migration added — and those
 * constraints, not the route, are what decide whether a row is allowed to
 * exist. A method on a kind that collects nothing, or an `other` with nothing
 * said about it, fails in Postgres or nowhere.
 *
 * It needs no provider fake, which is unusual here and is the point: recording
 * money the practice already took contacts no processor at all. A deployment
 * with no card processing configured can still run every step below, which is
 * exactly the practice the feature exists for.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

interface LedgerRow {
  kind: string
  amount_cents: number
  status: string
  method: string | null
  payment_reference: string | null
}

interface Balance {
  balance_cents: number
}

test("a cheque recorded in the chart reaches the ledger and moves the balance", async ({
  signedInPage: page,
  api,
}) => {
  const patient = await givePatient(api)

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByRole("tab", { name: /Balance/ }).click()

  await page.getByRole("button", { name: "Record payment" }).click()
  await page.getByLabel("Amount").fill("150.00")
  await page.getByRole("combobox", { name: /how it arrived/i }).click()
  await page.getByRole("option", { name: "Check" }).click()
  await page.getByLabel("Check number").fill("1042")
  await page.getByRole("button", { name: "Record payment" }).click()

  // The row the clinician just created, read back through the API rather than
  // off the screen: the question is what Postgres accepted, not what React
  // rendered.
  await expect
    .poll(async () => {
      const ledger = await api.get<LedgerRow[]>(`/api/patients/${patient.id}/charges`)
      return ledger.length
    })
    .toBe(1)

  const [row] = await api.get<LedgerRow[]>(`/api/patients/${patient.id}/charges`)
  expect(row).toMatchObject({
    kind: "payment",
    amount_cents: 15000,
    // Final on insert. No processor was called, so nothing will ever arrive
    // to move it off `pending` — a pending row here would never count.
    status: "succeeded",
    method: "check",
    payment_reference: "1042",
  })

  // Nothing was owed, so the money the practice took is money it now holds on
  // the client's behalf. A negative balance is the correct answer and the
  // arithmetic is not clamped to zero.
  const balance = await api.get<Balance>(`/api/patients/${patient.id}/balance`)
  expect(balance.balance_cents).toBe(-15000)

  await expect(page.getByText("check · 1042")).toBeVisible()
})

test("a payment recorded as 'other' has to say what it was", async ({ signedInPage: page, api }) => {
  const patient = await givePatient(api)

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByRole("tab", { name: /Balance/ }).click()
  await page.getByRole("button", { name: "Record payment" }).click()

  await page.getByLabel("Amount").fill("40.00")
  await page.getByRole("combobox", { name: /how it arrived/i }).click()
  await page.getByRole("option", { name: "Something else" }).click()
  await page.getByRole("button", { name: "Record payment" }).click()

  await expect(page.getByRole("alert")).toContainText(/how the money arrived/i)

  // Refused before anything was written — the ledger is untouched, not
  // holding a row the database would have rejected anyway.
  expect(await api.get<LedgerRow[]>(`/api/patients/${patient.id}/charges`)).toHaveLength(0)

  await page.getByLabel("How it arrived").fill("Zelle, 14 Mar")
  await page.getByRole("button", { name: "Record payment" }).click()

  await expect
    .poll(async () => {
      const ledger = await api.get<LedgerRow[]>(`/api/patients/${patient.id}/charges`)
      return ledger.length
    })
    .toBe(1)

  const [row] = await api.get<LedgerRow[]>(`/api/patients/${patient.id}/charges`)
  expect(row.method).toBe("other")
  expect(row.payment_reference).toBe("Zelle, 14 Mar")
})

test("a client with no card on file is told what they can still do", async ({
  signedInPage: page,
  api,
}) => {
  const patient = await givePatient(api)

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByRole("tab", { name: /Balance/ }).click()

  // The action is offered regardless of a card, a balance, or whether the
  // deployment has card processing set up at all. Before this existed, a
  // practice that takes cheques reached this tab and found nothing to press.
  await expect(page.getByRole("button", { name: "Record payment" })).toBeVisible()
})
