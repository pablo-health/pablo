// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Revoking an invitation cuts off a session that is already signed in, and
 * granting the address again lets it back in.
 *
 * A signed-in account resolves its practice from its membership, and that
 * answer is repeated from Redis for a minute
 * (backend/app/db/practice_resolution_cache.py). Revoking the grant deletes the
 * membership, and the cache entry is dropped after that commit. The database
 * tests drive the resolver against real Postgres with Redis faked; this spec is
 * the one place the revoke goes through the admin route against the stack's
 * real Redis (USE_REDIS=true in docker-compose.e2e.yml).
 *
 * The warm cache is what makes step 2 worth running. On a cold cache the next
 * request reads the database and is refused whether or not the drop works, so
 * the spec reads the account's entry straight out of Redis before the revoke
 * and refuses to go on without it. If the drop failed, the entry would answer
 * the next request for up to a minute and the invitee would still get in.
 *
 * Moving an account between practices is a revoke then a grant, so step 3
 * grants the address again and the same signed-in session gets back in.
 *
 * The invitation is into the second practice, not the shared one. A first
 * sign-in with no grant at all is mapped onto the deployment's own practice,
 * which is the shared one, so landing there would not show that the
 * invitation decided anything.
 */

import { execFileSync } from "node:child_process"
import { fileURLToPath } from "node:url"

import { ApiError, type ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { signInAs } from "../fixtures/freshPractice"

const COMPOSE_FILE = fileURLToPath(new URL("../../../docker-compose.e2e.yml", import.meta.url))
const PASSWORD = "E2e-invitee-password-long-enough"

/** The stack's Redis, asked directly. */
function redis(...args: string[]): string {
  return execFileSync(
    "docker",
    ["compose", "-f", COMPOSE_FILE, "exec", "-T", "redis", "redis-cli", ...args],
    { encoding: "utf8" },
  ).trim()
}

/** The account's membership entry in the resolution cache. */
function resolutionKey(userId: string): string {
  return `practice-resolution:v1:user:${userId}`
}

/** A practice-scoped read: refused with NO_PRACTICE when the account has none. */
async function readPatients(api: ApiClient): Promise<unknown> {
  return api.get("/api/patients")
}

async function expectNoPractice(api: ApiClient): Promise<void> {
  const refusal = await readPatients(api).then(
    () => null,
    (error: unknown) => error,
  )
  expect(refusal, "the revoked session still reached the practice").toBeInstanceOf(ApiError)
  expect((refusal as ApiError).status).toBe(403)
  expect((refusal as ApiError).body).toContain("NO_PRACTICE")
}

/** The practice an account resolves to, as the dashboard is told. */
async function practiceOf(api: ApiClient): Promise<string | undefined> {
  return (await api.get<{ practice_id?: string }>("/api/users/me/status")).practice_id
}

test("a revoke cuts off a signed-in invitee with a warm cache, and a re-grant lets them back", async ({
  browser,
  api: sharedPracticeApi,
  otherPracticeApi: admin,
}, testInfo) => {
  // A new address every attempt: a retry must start from an account that has
  // never signed in, or step 1 is not a first sign-in.
  const email = `e2e-invitee-${Date.now()}-${testInfo.retry}@example.com`
  const allowlistPath = `/api/admin/allowlist/${encodeURIComponent(email)}`
  let granted = false
  const practiceId = await practiceOf(admin)
  expect(practiceId).toBeTruthy()
  expect(practiceId, "the inviting practice must not be the shared one").not.toBe(
    await practiceOf(sharedPracticeApi),
  )

  try {
    // 1. Invited, then signed in through the real login page.
    await admin.post("/api/admin/allowlist", { email })
    granted = true
    const invitee = await signInAs(browser, email, PASSWORD)
    try {
      await expect(invitee.page).toHaveURL(/\/dashboard/)
      expect(await practiceOf(invitee.api)).toBe(practiceId)
      await readPatients(invitee.api)

      // 2. Warm the cache, see the entry in Redis, revoke, and the same
      // session is refused on its next request.
      const me = await invitee.api.get<{ id: string }>("/api/users/me")
      await readPatients(invitee.api)
      await readPatients(invitee.api)
      expect(
        redis("EXISTS", resolutionKey(me.id)),
        "the membership answer was not cached, so the revoke below would prove nothing",
      ).toBe("1")

      await admin.delete(allowlistPath)
      granted = false
      expect(redis("EXISTS", resolutionKey(me.id)), "the revoke left the cached answer").toBe("0")
      await expectNoPractice(invitee.api)
      // Not a one-off: the next request is refused too.
      await expectNoPractice(invitee.api)

      // 3. Granted again: the same session gets back in.
      await admin.post("/api/admin/allowlist", { email })
      granted = true
      await readPatients(invitee.api)
      expect(await practiceOf(invitee.api)).toBe(practiceId)
    } finally {
      await invitee.context.close()
    }
  } finally {
    // Leave no grant into the shared practice behind.
    if (granted) await admin.delete(allowlistPath)
  }
})
