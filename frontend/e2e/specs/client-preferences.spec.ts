// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "How to work with <client>" on the chart, end to end.
 *
 * - Adding "prefers afternoons" (Mon-Thu, afternoon, in person) shows it on
 *   the client's page with where it came from.
 * - Editing it to "only these times" is what the API then returns.
 * - Removing it takes it off the chart, and the record keeps it as retired.
 *
 * Isolation (a clinician without access reads and writes nothing) is proven
 * against the real row policy by the backend's integration test; this spec
 * proves the screen and the API agree.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Preference = {
  id: string
  kind: string
  strength: string | null
  value: { days: string[]; times: string[]; modality: string } | null
  retired_at: string | null
}

test("a client's preferred times are added, edited and removed on their chart", async ({
  signedInPage: page,
  api,
}) => {
  const client = await givePatient(api, { first_name: `Maya${Date.now().toString(36)}` })
  const base = `/api/patients/${client.id}/preferences`

  await page.goto(`/dashboard/patients/${client.id}`)
  const card = page.getByTestId("client-preferences")
  await expect(card.getByRole("heading", { name: `How to work with ${client.first_name}` })).toBeVisible()
  await expect(card).toContainText("Nothing added yet.")

  await card.getByRole("button", { name: "Add" }).click()
  const dialog = page.getByTestId("preference-dialog")
  await expect(dialog).toContainText("Clinical details belong in a note.")
  await dialog.getByRole("radio", { name: "Times that suit" }).check()
  for (const day of ["Mon", "Tue", "Wed", "Thu"]) {
    await dialog.getByRole("checkbox", { name: day }).check()
  }
  await dialog.getByRole("checkbox", { name: "Afternoon" }).check()
  await dialog.getByRole("radio", { name: "In person" }).check()
  await dialog.getByLabel("In your words (optional)").fill("School pickup in the mornings")

  const created = page.waitForResponse(
    (r) => new URL(r.url()).pathname === base && r.request().method() === "POST",
  )
  await dialog.getByRole("button", { name: "Save" }).click()
  const response = await created
  expect(response.status(), await response.text()).toBe(201)
  const preference = (await response.json()) as Preference
  expect(preference.kind).toBe("scheduling.availability")
  expect(preference.strength).toBe("soft")
  expect(preference.value).toEqual({
    days: ["mon", "tue", "wed", "thu"],
    times: ["afternoon"],
    modality: "in_person",
  })

  const row = card.getByTestId(`preference-${preference.id}`)
  await expect(row).toContainText("Prefers: Mon, Tue, Wed, Thu · Afternoon · In person")
  await expect(row).toContainText("School pickup in the mornings")
  await expect(row).toContainText(/You said, \w+ \d{4}/)

  // Edit: only these times.
  await row.getByRole("button", { name: "Edit" }).click()
  await dialog.getByRole("radio", { name: "Only these times" }).check()
  const edited = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === `${base}/${preference.id}` && r.request().method() === "PATCH",
  )
  await dialog.getByRole("button", { name: "Save" }).click()
  expect((await edited).status()).toBe(200)
  await expect(row).toContainText("Only: Mon, Tue, Wed, Thu · Afternoon · In person")

  // Remove: off the chart, kept as retired.
  const retired = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === `${base}/${preference.id}/retire` &&
      r.request().method() === "POST",
  )
  await row.getByRole("button", { name: "Remove" }).click()
  expect((await retired).status()).toBe(200)
  await expect(card.getByTestId(`preference-${preference.id}`)).toHaveCount(0)
  await expect(card).toContainText("Nothing added yet.")

  const kept = await api.get<{ data: Preference[] }>(`${base}?include_retired=true`)
  expect(kept.data.map((p) => [p.id, p.strength, p.retired_at !== null])).toEqual([
    [preference.id, "hard", true],
  ])
})
