// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice owner makes a colleague a practice admin from Settings, the
 * colleague can then change a practice setting, and turning it off takes that
 * away again.
 *
 * What only the real stack can prove: two real accounts resolve to one
 * practice, the owner's switch writes the grant into that practice's own
 * schema, and the colleague's next request is judged by it. The practice name
 * stands in for "a practice setting" because it is the one every clinician
 * sees, read-only unless they may change it.
 *
 * Its own seeded practice ("admins", with a colleague address): the shared
 * practice is registered under no owner address and so treats every clinician
 * as its owner, which would make the colleague's refusal impossible to see.
 *
 * Deliberately not here: an admin trying to grant, revoking the owner and the
 * audit rows, which tests_integration/api/test_practice_admin_routes.py covers
 * against a real schema in milliseconds.
 */

import { expect, test } from "../fixtures/auth"
import { ApiClient, ApiError, ensureEmulatorUser } from "../fixtures/api"
import { signInToFreshPractice } from "../fixtures/freshPractice"

const COLLEAGUE = {
  email: "e2e-fresh-admins-colleague@example.com",
  password: "E2e-fresh-admins-colleague-password-long-enough",
}

interface Clinician {
  user_id: string
  name: string | null
  email: string
  is_admin: boolean
}

async function renameStatus(api: ApiClient, name: string): Promise<number> {
  return api.patch("/api/users/me/professional-info", { practice_name: name }).then(
    () => 200,
    (error: unknown) => {
      if (error instanceof ApiError) return error.status
      throw error
    },
  )
}

test.describe("Practice admins", () => {
  test("the owner makes a colleague an admin, and takes it away", async ({ browser }) => {
    const owner = await signInToFreshPractice(browser, "admins")
    await ensureEmulatorUser(COLLEAGUE.email, COLLEAGUE.password)
    const colleague = await ApiClient.forUser(COLLEAGUE.email, COLLEAGUE.password)
    const original = (await colleague.get<{ practice_name?: string }>("/api/users/me/status"))
      .practice_name
    expect(original, "the colleague resolves to the seeded practice").toBe("Fresh Practice Admins")

    try {
      const listed = await owner.api.get<{ clinicians: Clinician[] }>("/api/practice/admins")
      const entry = listed.clinicians.find((c) => c.email === COLLEAGUE.email)
      expect(entry, "the colleague is a clinician of the practice").toBeDefined()
      // A retried run may find the grant from an earlier attempt.
      await owner.api.delete(`/api/practice/admins/${entry!.user_id}`)
      const label = `${entry!.name?.trim() || entry!.email} is an admin`

      expect(await renameStatus(colleague, "Renamed By Colleague")).toBe(403)

      await owner.page.goto("/dashboard/settings/profile")
      const toggle = owner.page.getByRole("switch", { name: label })
      await expect(toggle).toHaveAttribute("aria-checked", "false")
      await toggle.click()
      await expect(toggle).toHaveAttribute("aria-checked", "true")

      const status = await colleague.get<{ is_practice_owner?: boolean; is_practice_admin?: boolean }>(
        "/api/users/me/status",
      )
      expect(status).toMatchObject({ is_practice_owner: false, is_practice_admin: true })
      expect(await renameStatus(colleague, "Renamed By Colleague")).toBe(200)

      await owner.page.reload()
      await expect(owner.page.getByLabel("Practice name")).toHaveValue("Renamed By Colleague")
      await owner.page.getByRole("switch", { name: label }).click()
      await expect(owner.page.getByRole("switch", { name: label })).toHaveAttribute(
        "aria-checked",
        "false",
      )

      expect(await renameStatus(colleague, "Renamed Again")).toBe(403)
    } finally {
      await owner.api.patch("/api/users/me/professional-info", { practice_name: original })
      await owner.context.close()
    }
  })
})
