// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

test("a clinician authors, persists, and finalizes a manual SOAP note", async ({
  signedInPage: page,
  api,
}) => {
  const patient = await givePatient(api)
  const marker = `e2e-${Date.now().toString(36)}`
  const complaint = `Work-related worry ${marker}`

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByLabel("Notes").getByRole("button", { name: "New note" }).click()
  const dialog = page.getByRole("dialog")
  await dialog.getByRole("button", { name: /^SOAP\b/ }).click()
  await page.waitForURL(new RegExp(`/dashboard/patients/${patient.id}/notes/[0-9a-f-]+`))

  await expect(page.getByLabel("Chief Complaint")).toBeVisible()
  await page.getByLabel("Chief Complaint").fill(complaint)
  await page.getByLabel("Clinical Impression").fill(`Adjustment-related anxiety ${marker}`)
  await page.getByLabel("Next Steps").fill(`Practice paced breathing ${marker}`)

  const saved = page.waitForResponse(
    (response) =>
      /\/api\/notes\/[0-9a-f-]+$/.test(response.url()) &&
      response.request().method() === "PATCH" &&
      response.ok(),
  )
  await page.getByRole("button", { name: "Save Changes" }).click()
  await saved
  await page.reload()
  await expect(page.getByText(complaint)).toBeVisible()

  const finalized = page.waitForResponse(
    (response) => response.url().endsWith("/finalize") && response.ok(),
  )
  await page.getByRole("button", { name: /Finalize note/ }).click()
  await finalized
  await expect(page.getByText(/Finalized \d/)).toBeVisible()
  await expect(page.getByRole("button", { name: /^Edit$/ })).toHaveCount(0)
})
