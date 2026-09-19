// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "You can read your own audit log at any time through the application."
 *
 * The privacy policy says that in three places. The endpoint behind it has
 * worked for a long time; nothing in the product called it, so the sentence
 * was false in the only way that matters — there was no way to do it.
 *
 * This spec is the thing that keeps the sentence true: a signed-in user
 * reaching the page by navigating the product, seeing real rows written by
 * their own real activity, and getting past the first page. It runs against
 * the full local stack rather than a mocked client, because every part of the
 * claim — that the rows exist, that they are the caller's own, that paging
 * reaches older ones — is a property of the whole path, not of the component.
 */

import { expect, test } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"
import type { ApiClient } from "../fixtures/api"

const ACTIVITY = "/dashboard/settings/activity"

/** Create enough real activity that the trail has something to show. */
async function giveSomeActivity(api: ApiClient, count: number): Promise<void> {
  for (let i = 0; i < count; i += 1) {
    const patient = await givePatient(api)
    // Reading it back is itself an audited access — the rows this page exists
    // to show.
    await api.get(`/api/patients/${patient.id}`)
  }
}

test.describe("your own audit log", () => {
  test("is reachable from settings and shows your activity", async ({ signedInPage, api }) => {
    await giveSomeActivity(api, 2)

    // Navigate the way a user would, so the nav entry is under test too.
    await signedInPage.goto("/dashboard/settings/profile")
    await signedInPage.getByRole("link", { name: "Your activity" }).click()

    await expect(signedInPage).toHaveURL(new RegExp(`${ACTIVITY}$`))
    await expect(signedInPage.getByRole("heading", { name: "Your activity" })).toBeVisible()

    // The row, and the request context that makes it recognisable.
    const table = signedInPage.getByRole("table")
    await expect(table.getByText("Patient viewed").first()).toBeVisible()
    await expect(table.getByText("Patient created").first()).toBeVisible()
    await expect(signedInPage.getByRole("columnheader", { name: "IP address" })).toBeVisible()
    await expect(signedInPage.getByRole("columnheader", { name: "From" })).toBeVisible()
  })

  test("pages back past the first screenful instead of stopping at it", async ({
    signedInPage,
    api,
  }) => {
    // The page asks for 50 at a time. Rather than manufacture 50+ rows through
    // the UI, drive the API directly at a small limit: the contract under test
    // is that a full page hands back a cursor and the cursor reaches older
    // rows, which is the same contract "Load older" rides on.
    await giveSomeActivity(api, 3)

    const first = await api.get<{
      data: Array<{ id: string; timestamp: string }>
      next_cursor: string | null
    }>("/api/users/me/audit-log?limit=2")

    expect(first.data).toHaveLength(2)
    expect(first.next_cursor).toBeTruthy()

    const second = await api.get<{
      data: Array<{ id: string; timestamp: string }>
      next_cursor: string | null
    }>(`/api/users/me/audit-log?limit=2&cursor=${encodeURIComponent(first.next_cursor!)}`)

    const firstIds = first.data.map((row) => row.id)
    const secondIds = second.data.map((row) => row.id)
    expect(secondIds.some((id) => firstIds.includes(id))).toBe(false)
    // Older, not newer: paging goes back through history.
    expect(second.data[0].timestamp < first.data[1].timestamp).toBe(true)

    // And the screen offers the same journey.
    await signedInPage.goto(ACTIVITY)
    await expect(signedInPage.getByText(/Showing (the \d+ most recent|all \d+)/)).toBeVisible()
  })

  test("shows ids rather than names — the trail stays PHI-free", async ({ signedInPage, api }) => {
    // A distinctive name that must not reach a surface documented as
    // PHI-free. The row records WHICH record was touched, not who it is.
    const patient = await givePatient(api, {
      first_name: "Zebediah",
      last_name: "Quillfeather",
    })
    await api.get(`/api/patients/${patient.id}`)

    await signedInPage.goto(ACTIVITY)

    await expect(signedInPage.getByRole("table")).toBeVisible()
    await expect(signedInPage.getByText(patient.id, { exact: false }).first()).toBeVisible()
    await expect(signedInPage.getByText("Zebediah")).toHaveCount(0)
    await expect(signedInPage.getByText("Quillfeather")).toHaveCount(0)
  })

  test("reading the log does not make the log grow while you watch it", async ({
    signedInPage,
    api,
  }) => {
    await giveSomeActivity(api, 1)

    await signedInPage.goto(ACTIVITY)
    await expect(signedInPage.getByRole("table")).toBeVisible()
    const initial = await signedInPage.getByRole("row").count()

    // Blur and refocus: a view that refetched on focus would show its own
    // read events accumulating, which is the behaviour this page must not have.
    await signedInPage.evaluate(() => window.dispatchEvent(new Event("blur")))
    await signedInPage.evaluate(() => window.dispatchEvent(new Event("focus")))
    await signedInPage.waitForTimeout(500)

    expect(await signedInPage.getByRole("row").count()).toBe(initial)
  })
})
