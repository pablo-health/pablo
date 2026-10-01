// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice owner renames the practice from Settings, and the new name is
 * what the portal shows.
 *
 * What only the real stack can prove: the card saves through the real route
 * into the practice row, survives a reload, and reaches the portal's own name
 * for the practice (held beside the portal address, not on the practice row)
 * — while the signed BAA keeps the name it was signed under.
 *
 * Deliberately not here: the non-owner refusal, blank names and the audit
 * row, which cost milliseconds in backend/tests/test_user_profile.py.
 */

import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"

interface Status {
  practice_name?: string
  is_practice_owner?: boolean
}

interface BaaStatus {
  signed_practice_name?: string | null
}

async function practiceName(api: ApiClient): Promise<string> {
  const status = await api.get<Status>("/api/users/me/status")
  if (status.practice_name === undefined) throw new Error("no practice resolves for the e2e user")
  return status.practice_name
}

async function renameTo(api: ApiClient, name: string): Promise<void> {
  await api.patch("/api/users/me/professional-info", { practice_name: name })
}

test.describe("Renaming the practice", () => {
  let original: string

  test.beforeEach(async ({ api }) => {
    original = await practiceName(api)
  })

  test.afterEach(async ({ api }) => {
    // The practice is shared by every spec in this worker.
    await renameTo(api, original)
  })

  test("the owner renames it and the portal shows the new name", async ({
    api,
    signedInPage: page,
  }) => {
    const renamed = `Renamed Practice ${Date.now()}`
    const signedBefore = (await api.get<BaaStatus>("/api/users/me/baa-status"))
      .signed_practice_name

    await page.goto("/dashboard/settings/profile")
    const field = page.getByLabel("Practice name")
    await expect(field).toHaveValue(original)
    await expect(field).not.toHaveAttribute("readonly")

    await field.fill(renamed)
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByText("Saved.")).toBeVisible()

    await page.reload()
    await expect(page.getByLabel("Practice name")).toHaveValue(renamed)

    // The portal fills its name in from its own record of the practice.
    const welcome = await api.get<{ practice_name: string }>("/api/portal/welcome")
    expect(welcome.practice_name).toBe(renamed)

    // A rename leaves the agreement on file as it was signed.
    const signedAfter = (await api.get<BaaStatus>("/api/users/me/baa-status"))
      .signed_practice_name
    expect(signedAfter).toBe(signedBefore)
  })
})
