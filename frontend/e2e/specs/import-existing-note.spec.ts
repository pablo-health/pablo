// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

test("a clinician imports an existing note and it is filed for review", async ({
  signedInPage: page,
  api,
}) => {
  const patient = await givePatient(api)
  const marker = `e2e-${Date.now().toString(36)}`
  const complaint = `Trouble sleeping before work ${marker}`

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByLabel("Notes").getByRole("button", { name: "New note" }).click()
  await page.getByRole("dialog").getByRole("button", { name: /Import existing notes/ }).click()

  const dialog = page.getByRole("dialog", { name: "Import existing notes" })
  await dialog.getByLabel("Choose note files to import").setInputFiles({
    name: "prior-note.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(`Chief complaint: ${complaint}\n`),
  })

  const imported = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/patients/${patient.id}/sessions/import`) &&
      response.request().method() === "POST",
  )
  await dialog.getByRole("button", { name: "Import 1 note" }).click()
  const response = await imported
  expect(response.status()).toBe(201)
  expect(JSON.stringify(await response.json())).toContain(complaint)

  await expect(dialog.getByRole("status")).toHaveText("1 added")
  await expect(dialog.getByRole("button", { name: "Go to Review" })).toBeVisible()
})
