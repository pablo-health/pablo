// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A client's answer about AI-assisted notes, recorded from the chart header.
 *
 * The answer is a history, not a field: recording "agreed" and then
 * "declined" leaves both on the record, with the later one shown in the
 * header. Both survive a reload, so this proves the answer is stored rather
 * than held in the page.
 */

import { test, expect } from "../fixtures/auth"
import { BROWSER_TIME_ZONE } from "../fixtures/clock"
import { givePatient } from "../fixtures/scenarios"

/** A civil date some days back, as the date input takes it and as the chart
 * shows it. Computed from the browser's today (not this process's, which is
 * UTC) so the answer is never dated in the future. */
function daysAgo(days: number): { iso: string; shown: string } {
  const today = new Date().toLocaleDateString("en-CA", { timeZone: BROWSER_TIME_ZONE })
  const day = new Date(`${today}T00:00:00Z`)
  day.setUTCDate(day.getUTCDate() - days)
  return {
    iso: day.toISOString().slice(0, 10),
    shown: day.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      timeZone: "UTC",
    }),
  }
}

test("a clinician records a client's AI-notes answer and changes it", async ({
  signedInPage: page,
  api,
}) => {
  const patient = await givePatient(api)
  const agreedOn = daysAgo(5)
  const declinedOn = daysAgo(2)

  await page.goto(`/dashboard/patients/${patient.id}`)
  const line = page.getByTestId("ai-consent-line")
  await expect(line).toHaveText("AI notes: not asked yet")

  await line.click()
  const dialog = page.getByRole("dialog", { name: "AI notes" })
  await dialog.getByLabel("Client agreed").check()
  await dialog.getByLabel("Date").fill(agreedOn.iso)
  await dialog.getByRole("button", { name: "Save" }).click()
  await expect(dialog).toBeHidden()

  await page.reload()
  await expect(line).toHaveText(`AI notes: agreed ${agreedOn.shown}`)
  await line.click()
  const history = dialog.getByTestId("ai-consent-history").getByRole("listitem")
  await expect(history).toHaveCount(1)
  await expect(history.first()).toContainText(`Agreed ${agreedOn.shown}`)

  await dialog.getByLabel("Client declined").check()
  await dialog.getByLabel("Date").fill(declinedOn.iso)
  await dialog.getByRole("button", { name: "Save" }).click()
  await expect(dialog).toBeHidden()
  await expect(line).toHaveText(`AI notes: declined ${declinedOn.shown}`)

  await page.reload()
  await expect(line).toHaveText(`AI notes: declined ${declinedOn.shown}`)
  await line.click()
  await expect(history).toHaveCount(2)
  await expect(history.nth(0)).toContainText(`Declined ${declinedOn.shown}`)
  await expect(history.nth(1)).toContainText(`Agreed ${agreedOn.shown}`)
})

test("an answer keeps how it was given: telehealth, where the client was, and who answered", async ({
  signedInPage: page,
  api,
}) => {
  const patient = await givePatient(api)
  const today = daysAgo(0)

  await page.goto(`/dashboard/patients/${patient.id}`)
  const line = page.getByTestId("ai-consent-line")
  await line.click()
  const dialog = page.getByRole("dialog", { name: "AI notes" })
  await dialog.getByLabel("Client agreed").check()
  await dialog.getByLabel("Answered by").selectOption("parent")
  await dialog.getByLabel("Telehealth").check()
  await dialog.getByLabel("Where the client said they were").fill("At home")
  await dialog.getByRole("button", { name: "Save" }).click()
  await expect(dialog).toBeHidden()

  // Stored, not held in the page: read back after a reload and from the API.
  await page.reload()
  await line.click()
  const history = dialog.getByTestId("ai-consent-history").getByRole("listitem")
  await expect(history.first()).toContainText(
    `Agreed ${today.shown} · Telehealth · At home · Parent`,
  )
  const record = await api.get<{
    current: { modality: string; client_stated_location: string; consented_by: string }
  }>(`/api/patients/${patient.id}/ai-consent`)
  expect(record.current).toMatchObject({
    modality: "telehealth",
    client_stated_location: "At home",
    consented_by: "parent",
  })
})
