// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice that has never answered whether it offers the client portal.
 *
 * Some of the product only happens once, to a practice meeting a question for
 * the first time — the first-client portal prompt is the case this exists
 * for. The shared practice every worker signs into answered that long ago (the
 * worker fixture turns its portal on), so a spec that needs the first time
 * signs into one of these instead.
 *
 * The practices and their addresses are seeded at stack bring-up by
 * backend/scripts/e2e_seed_second_practice.py, which also clears their
 * portal answer on every bring-up. Each spec uses its own: the answer is
 * given once per run, so two specs sharing one would race for it.
 *
 * "messages", "feed", "domains" and "hosted" are practices of their own for a
 * different reason: the specs using each change something every other spec
 * would see — Messages turned off, a season of feed sessions on the calendar,
 * portal links that point at a host of the practice's own, a website
 * published with no host of the practice's own to put it on.
 */

import type { Browser, BrowserContext, BrowserContextOptions, Page } from "@playwright/test"
import { ApiClient, ensureEmulatorUser } from "./api"
import { BASE_URL } from "./stack"

export type FreshPracticeName =
  | "yes"
  | "no"
  | "messages"
  | "feed"
  | "domains"
  | "hosted"
  | "reserved"
  | "hours"
  | "consent"
  | "retention"

const PASSWORD = "E2e-fresh-practice-password-long-enough"

function addressOf(name: FreshPracticeName): string {
  return `e2e-fresh-${name}@example.com`
}

export interface FreshPractice {
  page: Page
  context: BrowserContext
  api: ApiClient
}

/**
 * Sign into the fresh practice *name* in a browser context of its own.
 *
 * A context made here gets none of the project's `use` options, so a spec
 * that pins the browser's timezone passes it in `options`.
 */
export async function signInToFreshPractice(
  browser: Browser,
  name: FreshPracticeName,
  options: Pick<BrowserContextOptions, "timezoneId" | "userAgent"> = {},
): Promise<FreshPractice> {
  const email = addressOf(name)
  await ensureEmulatorUser(email, PASSWORD)

  // An explicitly empty storage state. The test runner applies the project's
  // defaults to every context it makes, including the shared worker's saved
  // sign-in, so without this /login would redirect straight to the dashboard
  // as the worker's clinician — in the wrong practice.
  const context = await browser.newContext({
    ...options,
    baseURL: BASE_URL,
    storageState: { cookies: [], origins: [] },
  })
  const page = await context.newPage()
  await page.goto("/login")
  await page.getByLabel("Email").fill(email)
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD)
  await page.getByRole("button", { name: "Sign In", exact: true }).click()
  await page.waitForURL(/\/dashboard/)

  return { page, context, api: await ApiClient.forUser(email, PASSWORD) }
}
