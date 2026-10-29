// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A client who pays out of pocket asks for a superbill, and gets one.
 *
 * The superbill's unit tests build it from in-memory records. What they cannot
 * reach is the path a clinician takes: the chart's Balance tab, a visit no
 * claim was ever built for, the client's diagnosis setting saved to Postgres
 * and read back by the route, and a real PDF arriving in the browser. A
 * broken button, or a setting the route never sees, is invisible to them.
 *
 * Nothing here needs a provider fake: a superbill contacts no clearinghouse
 * and no card processor. The PDF is written uncompressed, so its text can be
 * read straight off the bytes; the figures themselves stay pinned by the unit
 * tests.
 */

import { readFile } from "node:fs/promises"

import type { Locator, Page } from "@playwright/test"

import { test, expect } from "../fixtures/auth"
import { givePatient, givePracticeReadyToBill, giveSessionWithCodes } from "../fixtures/scenarios"

/**
 * A visit that has already happened, about a day ago. Each test takes its own
 * hour: the calendar refuses two appointments that overlap, and every spec on
 * the stack books for the same clinician.
 */
function hoursAgo(hours: number): Date {
  return new Date(Date.now() - hours * 60 * 60 * 1000)
}

function isoDay(value: Date): string {
  return value.toISOString().slice(0, 10)
}

/** The visit's day, with a day of slack each side for the practice's timezone. */
function periodAround(visit: Date): { from: string; to: string } {
  const day = 24 * 60 * 60 * 1000
  return {
    from: isoDay(new Date(visit.getTime() - day)),
    to: isoDay(new Date(visit.getTime() + day)),
  }
}

/** Opens the chart's Superbill dialog on a period around ``visit``, and returns it. */
async function openSuperbill(page: Page, patientId: string, visit: Date): Promise<Locator> {
  await page.goto(`/dashboard/patients/${patientId}`)
  await page.getByRole("tab", { name: /Billing/ }).click()
  await page.getByRole("button", { name: "Superbill", exact: true }).click()
  const dialog = page.getByRole("dialog")
  const { from, to } = periodAround(visit)
  await dialog.getByLabel("From", { exact: true }).fill(from)
  await dialog.getByLabel("To", { exact: true }).fill(to)
  return dialog
}

async function downloadPdf(page: Page, dialog: Locator): Promise<string> {
  const download = page.waitForEvent("download")
  await dialog.getByRole("button", { name: /Download PDF/ }).click()
  return (await readFile(await (await download).path())).toString("latin1")
}

test("a self-pay visit with no claim prints, and the diagnosis only once it is turned on", async ({
  signedInPage: page,
  api,
}) => {
  await givePracticeReadyToBill(api)
  const patient = await givePatient(api, { date_of_birth: "1990-04-12", rate_cents: 11500 })
  const visit = hoursAgo(26)
  await giveSessionWithCodes(
    api,
    patient.id,
    { service_code: "90837", diagnosis_codes: ["F41.1"] },
    visit,
  )

  const first = await openSuperbill(page, patient.id, visit)
  await expect(first.getByLabel("Include the diagnosis")).not.toBeChecked()

  const receipt = await downloadPdf(page, first)
  expect(receipt.startsWith("%PDF")).toBe(true)
  expect(receipt).toContain("90837")
  expect(receipt).toContain("Therapy session")
  expect(receipt).toContain("$115.00")
  // Off by default: no column, and no code anywhere on the page.
  expect(receipt).not.toContain("Diagnosis")
  expect(receipt).not.toContain("F41.1")

  const second = await openSuperbill(page, patient.id, visit)
  await second.getByLabel("Include the diagnosis").click()

  // Saved on the client, not just ticked in the dialog: the route reads it
  // from the chart, so this is what decides what the next PDF carries.
  await expect
    .poll(async () => {
      const chart = await api.get<{ superbill_includes_diagnosis: boolean }>(
        `/api/patients/${patient.id}`,
      )
      return chart.superbill_includes_diagnosis
    })
    .toBe(true)

  const forTheInsurer = await downloadPdf(page, second)
  expect(forTheInsurer).toContain("F41.1")
})

test("a visit with no service code is named rather than downloaded blank", async ({
  signedInPage: page,
  api,
}) => {
  await givePracticeReadyToBill(api)
  const patient = await givePatient(api, { date_of_birth: "1990-04-12", rate_cents: 11500 })
  const visit = hoursAgo(28)
  await giveSessionWithCodes(api, patient.id, { service_code: "", diagnosis_codes: [] }, visit)

  const dialog = await openSuperbill(page, patient.id, visit)
  await dialog.getByRole("button", { name: /Download PDF/ }).click()

  await expect(dialog.getByRole("list", { name: "What is missing" })).toContainText(
    "has no service code. Add it to the appointment.",
  )
})
