// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * @practice-invite: a practice's owner invites a clinician by email, and the
 * account that opens the link joins that practice, even one that signs in
 * with a different address than the one invited.
 *
 * The account here has never used the app. On a deployment where anyone may
 * sign up, its first practice-resolving request would place it in the
 * deployment's own practice, and the redeem would then refuse it as already
 * in one. So landing in the inviting practice also proves the invitation page
 * redeemed before anything resolved the account.
 */

import { ApiClient, ensureEmulatorUser } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { freshPracticeApi } from "../fixtures/freshPractice"
import { firstLink, mail } from "../fixtures/mail"
import { BASE_URL } from "../fixtures/stack"

const PRACTICE_ID = "e2e-fresh-invites"
const PRACTICE_NAME = "Fresh Practice Invites"
const PASSWORD = "E2e-practice-invite-password-long-enough"

test.describe("@practice-invite an invitation joins whichever account accepts it", () => {
  test("an account with another address accepts after confirming, and opens the practice", async ({
    browser,
  }, testInfo) => {
    const run = `${Date.now()}-${testInfo.retry}`
    const invited = `e2e-invite-to-${run}@example.com`
    const joiner = `e2e-invite-joins-${run}@example.com`
    const owner = await freshPracticeApi("invites")

    const created = await owner.post<{ id: string; email_sent: boolean }>("/api/practice/invites", {
      email: invited,
    })
    expect(created.email_sent).toBe(true)
    const link = new URL(firstLink(await mail.waitFor(invited)))
    expect(link.pathname).toBe("/invite")
    expect(link.hash.length).toBeGreaterThan(1)

    await ensureEmulatorUser(joiner, PASSWORD)
    const context = await browser.newContext({
      baseURL: BASE_URL,
      storageState: { cookies: [], origins: [] },
    })
    try {
      const page = await context.newPage()
      await page.goto(`/invite${link.hash}`)
      await expect(
        page.getByRole("heading", { name: `You’ve been invited to join ${PRACTICE_NAME}` }),
      ).toBeVisible()
      await expect(page.getByText(`The invitation was sent to ${invited}.`)).toBeVisible()
      // The token leaves the address bar as soon as the page has it.
      expect(new URL(page.url()).hash).toBe("")

      await page.getByRole("link", { name: "Sign in to accept" }).click()
      await page.waitForURL(/\/login\?returnTo=%2Finvite/)
      await page.getByLabel("Email").fill(joiner)
      await page.getByLabel("Password", { exact: true }).fill(PASSWORD)
      await page.getByRole("button", { name: "Sign In", exact: true }).click()
      await page.waitForURL(/\/invite$/)

      await page.getByRole("button", { name: `Join ${PRACTICE_NAME}` }).click()
      await expect(
        page.getByRole("heading", { name: `Which account should join ${PRACTICE_NAME}?` }),
      ).toBeVisible()
      await expect(page.getByText(`sent to ${invited}, and you’re signed in as ${joiner}`)).toBeVisible()

      await page.getByRole("button", { name: `Join as ${joiner}` }).click()
      await page.waitForURL(/\/dashboard/)

      const joined = await ApiClient.forUser(joiner, PASSWORD)
      const status = await joined.get<{ practice_id?: string }>("/api/users/me/status")
      expect(status.practice_id).toBe(PRACTICE_ID)
      const pending = await owner.get<{ invites: { id: string }[] }>("/api/practice/invites")
      expect(pending.invites.map((invite) => invite.id)).not.toContain(created.id)

      // The link works once.
      await page.goto(`/invite${link.hash}`)
      await expect(
        page.getByRole("heading", { name: "This invitation can’t be used" }),
      ).toBeVisible()
    } finally {
      await context.close()
    }
  })
})
