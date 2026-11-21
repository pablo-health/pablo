// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * @membership: a signed-in account reaches the practice its membership names.
 *
 * The resolver (app.auth.service._resolve_practice) reads the account's
 * practice_members row first. The email mapping decides only for an account
 * with no membership, which is the first sign-in, and that sign-in claims the
 * membership. The database tests drive the resolver directly; this file drives
 * it through the real admin routes (POST and DELETE /api/admin/allowlist), the
 * real login page and the real API, so a change to which practice a request
 * reaches fails here, on the change that caused it.
 *
 * What each test reads:
 *  - the practice id /api/users/me/status reports, which comes from the same
 *    resolver the practice-scoped routes use;
 *  - NO_PRACTICE on /api/patients when the account should reach nothing;
 *  - a client the account itself added while in one practice. Most of this
 *    product is scoped per clinician inside a practice, so "another clinician
 *    cannot see mine" proves nothing about practices. The same clinician not
 *    seeing their own client after a move does: that client lives in the
 *    other practice's schema.
 *
 * The practices are three seeded for this file only
 * (backend/scripts/e2e_seed_second_practice.py). Every clinician is an admin
 * on the local stack, so a practice's owner is its admin, and what the owner
 * invites is invited into that practice. Each test uses an address of its own,
 * new on every attempt, so a retry starts from an account that has never
 * signed in.
 */

import { execFileSync } from "node:child_process"
import { fileURLToPath } from "node:url"

import type { Browser } from "@playwright/test"
import { ApiClient, ApiError, changeEmulatorEmail, ensureEmulatorUser } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { freshPracticeApi, signInAs } from "../fixtures/freshPractice"
import { givePatient, type Patient } from "../fixtures/scenarios"

const COMPOSE_FILE = fileURLToPath(new URL("../../../docker-compose.e2e.yml", import.meta.url))
const PASSWORD = "E2e-membership-password-long-enough"

const ONE = "e2e-fresh-membership-one"
const TWO = "e2e-fresh-membership-two"
const GONE = "e2e-fresh-membership-gone"

function compose(...args: string[]): string {
  return execFileSync("docker", ["compose", "-f", COMPOSE_FILE, "exec", "-T", ...args], {
    encoding: "utf8",
  }).trim()
}

/** Run SQL against the stack's database, as one transaction. */
function sql(statement: string): string {
  return compose(
    "postgres",
    "psql",
    "-U",
    "pablo",
    "-d",
    "pablo",
    "-v",
    "ON_ERROR_STOP=1",
    "-tAc",
    statement,
  )
}

/**
 * Drop every cached resolution filled for *practiceId*, account and address
 * entries alike, as a commit to its practices row does through the engine
 * (app.db.practice_resolution_cache). A write made with psql skips that.
 */
function dropCachedResolutions(practiceId: string): void {
  compose(
    "redis",
    "redis-cli",
    "EVAL",
    "local ks = redis.call('SMEMBERS', KEYS[1]) for _, k in ipairs(ks) do redis.call('DEL', k) end redis.call('DEL', KEYS[1]) return #ks",
    "1",
    `practice-resolution:v1:practice:${practiceId}`,
  )
}

function addressFor(scenario: string, retry: number): string {
  return `e2e-membership-${scenario}-${Date.now()}-${retry}@example.com`
}

function allowlistPath(email: string): string {
  return `/api/admin/allowlist/${encodeURIComponent(email)}`
}

/** The practice an account resolves to, as the dashboard is told. */
async function practiceOf(api: ApiClient): Promise<string | undefined> {
  return (await api.get<{ practice_id?: string }>("/api/users/me/status")).practice_id
}

async function patientIds(api: ApiClient): Promise<string[]> {
  const page = await api.get<{ data: Patient[] }>("/api/patients")
  return page.data.map((patient) => patient.id)
}

/** The account reaches no practice: practice-scoped reads are refused. */
async function expectNoPractice(api: ApiClient, why: string): Promise<void> {
  const refusal = await patientIds(api).then(
    () => null,
    (error: unknown) => error,
  )
  expect(refusal, why).toBeInstanceOf(ApiError)
  expect((refusal as ApiError).status).toBe(403)
  expect((refusal as ApiError).body).toContain("NO_PRACTICE")
  // Not /api/users/me/status: for an account with no practice it answers 500
  // today (it reads clinician_profiles with no practice schema), filed as its
  // own bug. The refusal above is the routing answer this file is about.
}

/** Sign in through the login page, and land on the dashboard of *practice*. */
async function signInTo(browser: Browser, email: string, practice: string): Promise<ApiClient> {
  const signedIn = await signInAs(browser, email, PASSWORD)
  try {
    await expect(signedIn.page).toHaveURL(/\/dashboard/)
    expect(await practiceOf(signedIn.api)).toBe(practice)
  } finally {
    await signedIn.context.close()
  }
  return signedIn.api
}

/** Grants made by a test, revoked whatever happens, so none outlives it. */
class Grants {
  private readonly open = new Map<string, ApiClient>()

  async grant(admin: ApiClient, email: string): Promise<void> {
    await admin.post("/api/admin/allowlist", { email })
    this.open.set(email, admin)
  }

  async revoke(email: string): Promise<void> {
    const admin = this.open.get(email)
    if (admin === undefined) throw new Error(`no open grant for ${email}`)
    await admin.delete(allowlistPath(email))
    this.open.delete(email)
  }

  async revokeAll(): Promise<void> {
    for (const email of [...this.open.keys()]) {
      await this.revoke(email).catch(() => undefined)
    }
  }
}

test.describe("@membership a signed-in account reaches the practice its membership names", () => {
  test("re-granted to a second practice, an account still reaches the first until it is revoked", async ({
    browser,
  }, testInfo) => {
    const one = await freshPracticeApi("membership-one")
    const two = await freshPracticeApi("membership-two")
    expect(await practiceOf(one)).toBe(ONE)
    expect(await practiceOf(two)).toBe(TWO)
    const email = addressFor("regrant", testInfo.retry)
    const grants = new Grants()

    try {
      // Granted into ONE, signed in: the first sign-in claims ONE.
      await grants.grant(one, email)
      const invitee = await signInTo(browser, email, ONE)
      const ownClient = await givePatient(invitee)
      expect(await patientIds(invitee)).toContain(ownClient.id)

      // TWO's admin grants the same address. The mapping moves to TWO; the
      // membership stays in ONE, and the membership decides.
      await two.post("/api/admin/allowlist", { email })
      expect(await practiceOf(invitee)).toBe(ONE)
      expect(await patientIds(invitee)).toContain(ownClient.id)
      // A fresh sign-in, with a token minted after the re-grant, too.
      const again = await signInTo(browser, email, ONE)
      expect(await patientIds(again)).toContain(ownClient.id)

      // Revoked: the grant, the mapping and the membership it led to go,
      // whichever admin made the grant last. The account reaches nothing.
      await grants.revoke(email)
      await expectNoPractice(again, "a revoked account still reached a practice")

      // Granted into TWO afresh: the next request claims TWO, and the client
      // the account added in ONE is not there.
      await grants.grant(two, email)
      expect(await practiceOf(again)).toBe(TWO)
      expect(await patientIds(again)).not.toContain(ownClient.id)
      const inTwo = await signInTo(browser, email, TWO)
      expect(await patientIds(inTwo)).not.toContain(ownClient.id)
    } finally {
      await grants.revokeAll()
    }
  })

  test("a granted account reaches its practice on the first sign-in and on the next", async ({
    browser,
  }, testInfo) => {
    const one = await freshPracticeApi("membership-one")
    const two = await freshPracticeApi("membership-two")
    const email = addressFor("grant", testInfo.retry)
    const grants = new Grants()

    try {
      await grants.grant(one, email)
      const first = await signInTo(browser, email, ONE)
      const ownClient = await givePatient(first)

      const second = await signInTo(browser, email, ONE)
      expect(await patientIds(second)).toContain(ownClient.id)
      // TWO's own clinician does not see it: a different practice's schema.
      expect(await patientIds(two)).not.toContain(ownClient.id)
    } finally {
      await grants.revokeAll()
    }
  })

  test("a revoked account reaches no practice, on its open session or a new token", async ({
    browser,
  }, testInfo) => {
    const one = await freshPracticeApi("membership-one")
    const email = addressFor("revoke", testInfo.retry)
    const grants = new Grants()

    try {
      await grants.grant(one, email)
      const invitee = await signInTo(browser, email, ONE)
      await grants.revoke(email)

      await expectNoPractice(invitee, "the open session still reached the practice")
      const fresh = await ApiClient.forUser(email, PASSWORD)
      await expectNoPractice(fresh, "a new token still reached the practice")
    } finally {
      await grants.revokeAll()
    }
  })

  test("an account whose address changed keeps its practice, and loses it when the original grant is revoked", async ({
    browser,
  }, testInfo) => {
    const one = await freshPracticeApi("membership-one")
    const original = addressFor("address-old", testInfo.retry)
    const changed = addressFor("address-new", testInfo.retry)
    const grants = new Grants()

    try {
      await grants.grant(one, original)
      const { uid } = await ensureEmulatorUser(original, PASSWORD)
      await signInTo(browser, original, ONE)

      // The provider now knows the account by a new address, which no admin
      // ever granted. The account is the same uid, so its membership holds.
      await changeEmulatorEmail(uid, changed)
      const renamed = await signInTo(browser, changed, ONE)

      // Revoking the address the membership was claimed with ends it, even
      // though the account no longer signs in with that address.
      await grants.revoke(original)
      await expectNoPractice(renamed, "revoking the original address left the account in")
      await expectNoPractice(
        await ApiClient.forUser(changed, PASSWORD),
        "a new token under the changed address still reached the practice",
      )
    } finally {
      await grants.revokeAll()
    }
  })

  test("a member of a deactivated practice reaches no practice, and reaches the next one it is granted", async ({
    browser,
  }, testInfo) => {
    const gone = await freshPracticeApi("membership-gone")
    const two = await freshPracticeApi("membership-two")
    const email = addressFor("deactivated", testInfo.retry)
    const grants = new Grants()
    let deactivated = false

    try {
      await grants.grant(gone, email)
      const member = await signInTo(browser, email, GONE)

      // Deactivated with its membership left in place. The engine's own
      // deactivation removes memberships in the same transaction
      // (practice_members.remove_for_practice), but the resolver must not
      // depend on that. There is no deactivation route on this stack, so the
      // row is changed directly and the practice's cached answers, which a
      // commit through the engine would drop, are dropped here.
      sql(`UPDATE platform.practices SET is_active = false WHERE id = '${GONE}'`)
      deactivated = true
      dropCachedResolutions(GONE)
      await expectNoPractice(member, "a member of an inactive practice still reached it")

      // TWO's admin grants the address, so it now maps to a LIVE practice. The
      // membership in the inactive practice still decides, and decides
      // "nothing": a resolver that fell through to the mapping would reach TWO
      // here. Before the membership decided, it did.
      await two.post("/api/admin/allowlist", { email })
      await expectNoPractice(member, "an inactive membership fell through to the address mapping")
      await expectNoPractice(
        await ApiClient.forUser(email, PASSWORD),
        "a new token fell through to the address mapping",
      )

      // As remove_for_practice leaves it: the member's own row is gone. The
      // account has no membership, so its address decides, and it reaches TWO.
      sql(
        `DELETE FROM platform.practice_members WHERE practice_id = '${GONE}' AND claimed_email = '${email}'`,
      )
      dropCachedResolutions(GONE)
      expect(await practiceOf(member)).toBe(TWO)
      await signInTo(browser, email, TWO)
    } finally {
      await grants.revokeAll()
      // The grant into TWO was made by TWO's admin; revoke it as well.
      await two.delete(allowlistPath(email)).catch(() => undefined)
      if (deactivated) {
        sql(`UPDATE platform.practices SET is_active = true WHERE id = '${GONE}'`)
        dropCachedResolutions(GONE)
      }
    }
  })

  // Fails today, before and after the membership change: a revoke landing
  // after the first request passed the allowlist gate lets that request's
  // auto-provisioning map the revoked address onto the default practice
  // (PABLO-3iqd.9). Remove the fixme with that fix.
  test.fixme("a revoke racing a first sign-in leaves no account in a practice it has no grant for", async ({}, testInfo) => {
    const one = await freshPracticeApi("membership-one")
    const runs = 20

    for (let run = 0; run < runs; run++) {
      const email = addressFor(`race-${run}`, testInfo.retry)
      await ensureEmulatorUser(email, PASSWORD)
      // The token is minted before the race: signing in with the provider
      // touches no backend. The first request carrying it is the first sign-in
      // the backend sees, the one that claims a membership.
      const invitee = await ApiClient.forUser(email, PASSWORD)
      const grants = new Grants()

      try {
        await grants.grant(one, email)
        // The revoke starts a little later on each run, so the twenty runs
        // land it at different points of the first request's claim.
        const delay = run * 5
        const [firstSignIn, revoked] = await Promise.allSettled([
          patientIds(invitee),
          new Promise((resolve) => setTimeout(resolve, delay)).then(() => grants.revoke(email)),
        ])
        if (revoked.status === "rejected") throw revoked.reason
        // Either side may win, so the first request may reach ONE or nothing.
        // Anything other than a NO_PRACTICE refusal is a real failure.
        if (firstSignIn.status === "rejected") {
          const error = firstSignIn.reason as unknown
          expect(error, `run ${run}: first sign-in failed oddly`).toBeInstanceOf(ApiError)
          expect((error as ApiError).body).toContain("NO_PRACTICE")
        }

        // After the revoke has answered, the account holds no grant, so it
        // reaches nothing: on the token that raced and on a new one.
        await expectNoPractice(
          invitee,
          `run ${run} (revoke after ${delay}ms): the raced session kept a practice`,
        )
        await expectNoPractice(
          await ApiClient.forUser(email, PASSWORD),
          `run ${run} (revoke after ${delay}ms): a new token reached a practice`,
        )
      } finally {
        await grants.revokeAll()
      }
    }
  })
})
