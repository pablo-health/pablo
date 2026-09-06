// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tiered auth fixtures.
 *
 * `onboardedUser` (once per worker): create a user on the Firebase Auth
 * emulator, sign in through the product's real /login page so the login
 * flow itself is under test once per run, and save the browser state —
 * cookies for the server-rendered pages, IndexedDB for the Firebase SDK's
 * persisted session.
 *
 * `signedInPage` (per test): a page whose context starts from that saved
 * state, so a spec begins already signed in. `api` is a bearer client for
 * the same user, for "given X" setup calls.
 */

import { test as base, expect, type Page } from "@playwright/test"
import { mkdirSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { ApiClient, createEmulatorUser } from "./api"
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
}

interface TestFixtures {
  signedInPage: Page
  api: ApiClient
}

const AUTH_STATE_DIR = fileURLToPath(new URL("../.auth/", import.meta.url))

export const test = base.extend<TestFixtures, WorkerFixtures>({
  onboardedUser: [
    async ({ browser }, provide, workerInfo) => {
      const stamp = `${Date.now().toString(36)}-${workerInfo.workerIndex}`
      const email = `e2e-${stamp}@example.com`
      const password = `E2e-password-${stamp}-long-enough`
      const { uid } = await createEmulatorUser(email, password)

      const context = await browser.newContext({ baseURL: BASE_URL })
      const page = await context.newPage()
      await page.goto("/login")
      await page.getByLabel("Email").fill(email)
      await page.getByLabel("Password", { exact: true }).fill(password)
      await page.getByRole("button", { name: "Sign In", exact: true }).click()
      await page.waitForURL(/\/dashboard/)

      mkdirSync(AUTH_STATE_DIR, { recursive: true })
      const storageStatePath = path.join(AUTH_STATE_DIR, `user-${workerInfo.workerIndex}.json`)
      await context.storageState({ path: storageStatePath, indexedDB: true })
      await context.close()

      await provide({ email, password, uid, storageStatePath })
    },
    { scope: "worker" },
  ],

  storageState: async ({ onboardedUser }, provide) => {
    await provide(onboardedUser.storageStatePath)
  },

  signedInPage: async ({ page }, provide) => {
    await provide(page)
  },

  api: async ({ onboardedUser }, provide) => {
    await provide(await ApiClient.forUser(onboardedUser.email, onboardedUser.password))
  },
})

export { expect }
