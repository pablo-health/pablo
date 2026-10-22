// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The waitlist over the real stack. A clinician adds a client from the
 * client's page, choosing days, a time of day and a format; the page then
 * shows the client on the list. In Settings > Waitlist the client is listed
 * with what they asked for, and moving them down past a second client sends
 * the new order, which the server keeps.
 *
 * A client on hold cannot be added: the page's action is disabled.
 *
 * Isolation between clinicians is proven by the backend's route tests; this
 * spec proves the screens and the routes agree.
 */

import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { givePatient } from "../fixtures/scenarios"

interface Entry {
  id: string
  patient_id: string
  position: number
}

const tag = () => Date.now().toString(36)

async function openEntryIds(api: ApiClient): Promise<string[]> {
  const list = await api.get<{ entries: Entry[] }>("/api/waitlist")
  return list.entries.map((e) => e.id)
}

test("a client added from their page is on the waitlist and can be moved", async ({
  signedInPage: page,
  api,
}) => {
  const first = await givePatient(api, { first_name: `Wren${tag()}` })
  const second = await givePatient(api, { first_name: `Ines${tag()}` })
  const firstName = `${first.first_name} ${first.last_name}`
  const secondName = `${second.first_name} ${second.last_name}`
  const added: string[] = []
  try {
    // From the client's page.
    await page.goto(`/dashboard/patients/${first.id}`)
    await page.getByRole("button", { name: "Add to waitlist" }).click()
    const dialog = page.getByTestId("waitlist-dialog")
    await dialog.getByRole("checkbox", { name: "Tue" }).check()
    await dialog.getByRole("checkbox", { name: "Thu" }).check()
    await dialog.getByRole("checkbox", { name: "Evening" }).check()
    // No past visits, so the format is not guessed.
    await expect(dialog.getByRole("radio", { name: "Video" })).not.toBeChecked()
    await dialog.getByRole("radio", { name: "Video" }).check()

    const created = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === `/api/patients/${first.id}/waitlist` &&
        r.request().method() === "POST",
    )
    await dialog.getByTestId("waitlist-save").click()
    const response = await created
    expect(response.status(), await response.text()).toBe(201)
    const entry = (await response.json()) as Entry
    added.push(entry.id)

    await expect(page.getByRole("button", { name: "On your waitlist" })).toBeVisible()

    // A second client, straight after on the list.
    const next = await api.post<Entry>(`/api/patients/${second.id}/waitlist`, {
      days: ["mon"],
      times: ["morning"],
      modality: "in_person",
    })
    added.push(next.id)
    expect(next.position).toBe(entry.position + 1)

    // In settings, in that order, with what each asked for.
    await page.goto("/dashboard/settings/waitlist")
    const rows = page.getByTestId("waitlist-entry")
    const firstRow = rows.filter({ hasText: firstName })
    const secondRow = rows.filter({ hasText: secondName })
    await expect(firstRow.getByTestId("waitlist-entry-summary")).toHaveText(
      "Tue, Thu · Evening · Video",
    )
    await expect(firstRow.getByTestId("waitlist-entry-position")).toHaveText(
      String(entry.position),
    )
    await expect(secondRow.getByTestId("waitlist-entry-position")).toHaveText(
      String(next.position),
    )

    // Move the first client down, past the second.
    const reordered = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/waitlist/order" && r.request().method() === "PUT",
    )
    await page.getByRole("button", { name: `Move ${firstName} down` }).click()
    expect((await reordered).status()).toBe(200)

    await expect(secondRow.getByTestId("waitlist-entry-position")).toHaveText(
      String(entry.position),
    )
    await expect(firstRow.getByTestId("waitlist-entry-position")).toHaveText(
      String(next.position),
    )

    // The server kept it.
    const order = await openEntryIds(api)
    expect(order.indexOf(next.id)).toBe(order.indexOf(entry.id) - 1)

    // And it holds after a reload.
    await page.reload()
    const ids = await page
      .getByTestId("waitlist-entry")
      .evaluateAll((els) => els.map((el) => el.getAttribute("data-entry-id")))
    expect(ids.indexOf(next.id)).toBe(ids.indexOf(entry.id) - 1)
  } finally {
    for (const id of added) {
      await api.delete(`/api/waitlist/${id}`).catch(() => undefined)
    }
  }
})

test("a client on hold cannot be added to the waitlist", async ({ signedInPage: page, api }) => {
  const client = await givePatient(api, { first_name: `Hold${tag()}`, status: "on_hold" })

  await page.goto(`/dashboard/patients/${client.id}`)
  await expect(page.getByTestId("waitlist-add")).toBeDisabled()
})
