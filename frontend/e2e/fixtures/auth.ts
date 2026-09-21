// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tiered auth fixtures.
 *
 * `onboardedUser` (once per worker): create-or-reuse this worker slot's
 * clinician on the Firebase Auth emulator (see `workerClinician` for why the
 * address is fixed rather than generated), sign in through the product's real
 * /login page so the login flow itself is under test once per run, clear the
 * diary that account is carrying (see `clearDiary`), and save the browser
 * state —
 * cookies for the server-rendered pages, IndexedDB for the Firebase SDK's
 * persisted session.
 *
 * `signedInPage` (per test): a page whose context starts from that saved
 * state, so a spec begins already signed in. `api` is a bearer client for
 * the same user, for "given X" setup calls.
 *
 * `otherPracticeApi` (per test): a bearer client for a user in a DIFFERENT
 * practice, seeded at stack bring-up by
 * backend/scripts/e2e_seed_second_practice.py. Only tenant-isolation specs
 * should want it, and they must assert in BOTH directions — most of this
 * product is scoped per user inside a practice, so "the other one cannot see
 * mine" is true whether or not tenancy works. See
 * specs/tenant-isolation.spec.ts, which explains the trap in full.
 */

import { test as base, expect, type Page } from "@playwright/test"
import { mkdirSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { ApiClient, ensureEmulatorUser, signInWithPassword } from "./api"
import { attachServerErrorGuard } from "./serverErrorGuard"
import { BASE_URL } from "./stack"

export interface E2EUser {
  email: string
  password: string
  uid: string
  /** Saved browser state for a context that is already signed in. */
  storageStatePath: string
}

interface WorkerFixtures {
  onboardedUser: E2EUser
  otherPracticeUser: E2EUser
}

interface TestFixtures {
  signedInPage: Page
  api: ApiClient
  otherPracticeApi: ApiClient
}

/**
 * Seeded by backend/scripts/e2e_seed_second_practice.py at stack bring-up,
 * which writes this address's tenant mapping BEFORE it ever signs in. That
 * ordering is the whole mechanism: the auto-provision path never overwrites an
 * existing mapping, so this user lands in the second practice while everyone
 * else lands in the default one.
 *
 * Fixed, not generated, because the seed script and this file have to agree on
 * it without talking. Safe because the suite runs workers: 1 and `make
 * e2e-down` drops the volume between runs.
 */
const SECOND_PRACTICE_EMAIL = "e2e-second-practice@example.com"
const SECOND_PRACTICE_PASSWORD = "E2e-second-practice-password-long-enough"

const AUTH_STATE_DIR = fileURLToPath(new URL("../.auth/", import.meta.url))

/**
 * The clinician a worker slot signs in as — the same one every time that slot
 * runs, including after a restart.
 *
 * A generated address looks safer and is the reason the booking journey went
 * red. A practice records its owner the first time somebody signs into it, and
 * that is a one-shot claim: the column is filled once and never reassigned.
 * Every surface that resolves "the clinician whose diary this is" then answers
 * for that first account — the patient booking routes above all, which compute
 * openings against the practice owner rather than against whoever seeded the
 * hours.
 *
 * Playwright starts a fresh worker after a failing test and re-runs every
 * worker-scoped fixture, so a generated address meant one unrelated failure
 * anywhere in the file list quietly moved the rest of the run onto a second
 * clinician who did not own the practice. Availability seeded by that account
 * was invisible to the booking surface, which answered "nothing open" for
 * every day a patient walked to — a failure that names the picker and points
 * nowhere near the sign-in that caused it.
 *
 * Keyed on `parallelIndex` rather than `workerIndex` for exactly that reason:
 * the parallel slot is reused when a worker restarts, while the worker index
 * keeps counting. `make e2e-down` drops the volume between runs, so the first
 * sign-in of a fresh stack still creates a fresh practice and a fresh account.
 */
function workerClinician(parallelIndex: number): { email: string; password: string } {
  return {
    email: `e2e-clinician-${parallelIndex}@example.com`,
    password: `E2e-clinician-${parallelIndex}-password-long-enough`,
  }
}

/**
 * Hand the worker the empty diary a brand-new account used to arrive with.
 *
 * Reusing the slot's clinician is what keeps the practice's owner stable, and
 * it would otherwise hand each worker whatever the last one left behind. That
 * matters because specs were written against a fresh account: one books a
 * session minutes from now and needs no working hours standing in the way,
 * another seeds a day's hours and counts the openings it gets back — a second
 * rule for the same day offers every opening twice.
 *
 * Availability rules and the diary are what a restart used to clear and what
 * these specs actually read, so they are what is cleared here. Everything
 * else already accumulates across a run, because every test in a worker
 * shares this account whether or not anything restarted.
 *
 * Deleting an appointment cancels it, which is enough: a cancelled hour
 * conflicts with nothing and belongs to a patient no later test shares.
 * On the first start of a run the account is new and this finds nothing.
 */
async function clearDiary(api: ApiClient): Promise<void> {
  const rules = await api.get<{ data: { id: string }[] }>("/api/availability/rules")
  for (const rule of rules.data) {
    await api.delete(`/api/availability/rules/${rule.id}`)
  }

  const year = 365 * 24 * 60 * 60 * 1000
  const from = new Date(Date.now() - year).toISOString()
  const until = new Date(Date.now() + year).toISOString()
  const diary = await api.get<{ data: { id: string; status: string }[] }>(
    `/api/appointments?start=${encodeURIComponent(from)}&end=${encodeURIComponent(until)}`,
  )
  for (const appointment of diary.data) {
    if (appointment.status !== "cancelled") await api.delete(`/api/appointments/${appointment.id}`)
  }
}

export const test = base.extend<TestFixtures, WorkerFixtures>({
  onboardedUser: [
    async ({ browser }, provide, workerInfo) => {
      const { email, password } = workerClinician(workerInfo.parallelIndex)
      const { uid } = await ensureEmulatorUser(email, password)

      const context = await browser.newContext({ baseURL: BASE_URL })
      const page = await context.newPage()
      await page.goto("/login")
      await page.getByLabel("Email").fill(email)
      await page.getByLabel("Password", { exact: true }).fill(password)
      await page.getByRole("button", { name: "Sign In", exact: true }).click()
      await page.waitForURL(/\/dashboard/)

      mkdirSync(AUTH_STATE_DIR, { recursive: true })
      const storageStatePath = path.join(AUTH_STATE_DIR, `user-${workerInfo.parallelIndex}.json`)
      await context.storageState({ path: storageStatePath, indexedDB: true })
      await context.close()

      await clearDiary(await ApiClient.forUser(email, password))

      await provide({ email, password, uid, storageStatePath })
    },
    { scope: "worker" },
  ],

  otherPracticeUser: [
    async ({}, provide) => {
      // Create-or-reuse, not create: this address is fixed, and Playwright
      // starts a fresh worker after a failing test, which re-runs every
      // worker-scoped fixture. A plain create would then hit EMAIL_EXISTS and
      // turn one red test into a red file.
      const { uid } = await ensureEmulatorUser(
        SECOND_PRACTICE_EMAIL,
        SECOND_PRACTICE_PASSWORD,
      )
      // No UI sign-in and no storage state: isolation is asserted at the API,
      // where the tenant boundary actually lives. Driving a second browser
      // context through /login would prove the login page works twice.
      await provide({
        email: SECOND_PRACTICE_EMAIL,
        password: SECOND_PRACTICE_PASSWORD,
        uid,
        storageStatePath: "",
      })
    },
    { scope: "worker" },
  ],

  otherPracticeApi: async ({ otherPracticeUser }, provide) => {
    await provide(
      new ApiClient(
        await signInWithPassword(otherPracticeUser.email, otherPracticeUser.password),
      ),
    )
  },

  storageState: async ({ onboardedUser }, provide) => {
    await provide(onboardedUser.storageStatePath)
  },

  signedInPage: async ({ page }, provide) => {
    // Watch the network, not just the rendered result: a page-load fan-out can
    // lose a panel to a 500 and still satisfy every assertion in a spec.
    const serverErrors = attachServerErrorGuard(page)
    try {
      await provide(page)
    } finally {
      serverErrors.dispose()
      // After the spec body, so a spec's own failure surfaces first — this is
      // extra information about a run, not a competing verdict.
      serverErrors.assertNone()
    }
  },

  api: async ({ onboardedUser }, provide) => {
    await provide(await ApiClient.forUser(onboardedUser.email, onboardedUser.password))
  },
})

export { expect }
