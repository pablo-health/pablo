// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

test("the patient chart renders without browser errors", async ({ signedInPage: page, api }) => {
  const patient = await givePatient(api)
  const pageErrors: string[] = []
  const consoleErrors: string[] = []
  page.on("pageerror", (error) => pageErrors.push(error.message))
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      consoleErrors.push(message.text())
    }
  })

  await page.goto(`/dashboard/patients/${patient.id}`)
  await expect(
    page.getByRole("heading", { name: `${patient.first_name} ${patient.last_name}` }),
  ).toBeVisible()
  await expect(page.getByRole("heading", { name: "Chart", exact: true })).toBeVisible()
  await expect(page.getByRole("tab", { name: /Notes/ })).toBeVisible()
  await expect(page.getByRole("tab", { name: /Documents/ })).toBeVisible()
  expect(pageErrors).toEqual([])
  expect(consoleErrors).toEqual([])
})
