// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Setting a practice up from empty, the way a therapist does it on day one.
 *
 * The state a new user meets is the one state nothing else exercises. Every
 * other billing spec runs against a practice that is already configured, and
 * two facts conspire to keep it that way:
 *
 *   1. The onboarded account is WORKER-SCOPED and shared across the run.
 *   2. More importantly, the billing profile belongs to the PRACTICE, not to
 *      the user — and every account in this stack lands in the same practice.
 *      A brand-new sign-up therefore inherits whatever the last spec wrote.
 *
 * So "make a new user" does not make a new practice, and there is currently no
 * way to get one. Until there is, this spec empties what it can and puts it
 * back afterwards — the tax ID excepted, which is write-only and can be
 * replaced but never cleared. That is safe only because the suite runs
 * `workers: 1, fullyParallel: false`; if it ever runs specs concurrently this
 * has to become real per-practice provisioning, and the restore below stops
 * being enough.
 *
 * It drives the UI throughout and asserts the RECORD afterwards, rather than
 * seeding the record and checking the screen agrees. Seeding would skip the
 * thing under test: whether a therapist can get her practice set up at all.
 */

import { randomUUID } from "node:crypto"
import { type Browser, type Page, type Response, expect, test } from "@playwright/test"
import { ApiClient, createEmulatorUser } from "../fixtures/api"
import { BASE_URL } from "../fixtures/stack"

interface BillingProfile {
  legal_name: string | null
  tax_id_last4: string | null
  tax_id_type: string | null
  address_line1: string | null
  city: string | null
  state: string | null
  postal_code: string | null
  phone: string | null
  contact_email: string | null
  billing_npi: string | null
}

interface Preferences {
  billing_setup_route?: string | null
  billing_setup_step?: string | null
  billing_setup_complete?: boolean
}

const EMPTY: Partial<BillingProfile> = {
  legal_name: null,
  billing_npi: null,
  address_line1: null,
  city: null,
  state: null,
  postal_code: null,
  phone: null,
  contact_email: null,
}

/** A fresh account, signed in, with the practice emptied around the test. */
async function withNewPractice(
  browser: Browser,
  body: (page: Page, api: ApiClient) => Promise<void>,
) {
  // randomUUID rather than Math.random: this string becomes a password, and a
  // predictable generator for one is a bad pattern to leave in the tree even
  // where the account is a throwaway on a local emulator.
  const stamp = `${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`
  const email = `e2e-new-${stamp}@example.com`
  const password = `E2e-password-${stamp}-long-enough`
  await createEmulatorUser(email, password)

  const context = await browser.newContext({ baseURL: BASE_URL })
  const page = await context.newPage()
  const api = await ApiClient.forUser(email, password)
  const restore = await api.get<BillingProfile>("/api/practice/billing-profile")

  try {
    await api.patch("/api/practice/billing-profile", EMPTY)

    await page.goto("/login")
    await page.getByLabel("Email").fill(email)
    await page.getByLabel("Password", { exact: true }).fill(password)
    await page.getByRole("button", { name: "Sign In", exact: true }).click()
    await page.waitForURL(/\/dashboard/)

    await body(page, api)
  } finally {
    // Put the shared practice back as it was, whatever happened above.
    await api.patch("/api/practice/billing-profile", {
      legal_name: restore.legal_name,
      billing_npi: restore.billing_npi,
      address_line1: restore.address_line1,
      city: restore.city,
      state: restore.state,
      postal_code: restore.postal_code,
      phone: restore.phone,
      contact_email: restore.contact_email,
    })
    await context.close()
  }
}

test("a practice with nothing on file sets itself up through the wizard", async ({ browser }) => {
  await withNewPractice(browser, async (page, api) => {
    const before = await api.get<BillingProfile>("/api/practice/billing-profile")
    expect(before.legal_name).toBeNull()
    expect(before.city).toBeNull()

    await page.goto("/dashboard/billing/setup")

    // Screen 1. Private pay is the shortest honest path through setup.
    await page.getByText("My clients pay me directly").click()

    // Screen 2, on every route including this one: the NPI lookup. She bills
    // nobody and still needs it, because a superbill carries the rendering
    // provider's NPI. This practice has none on file, so the field is empty
    // and she is offered the two ways out rather than being stopped.
    await expect(page.getByRole("heading", { name: "Let's start with your NPI" })).toBeVisible()
    await expect(page.getByLabel("Your individual NPI")).toHaveValue("")
    await expect(page.getByRole("button", { name: /don.t have one/i })).toBeVisible()
    await page.getByRole("button", { name: "Continue" }).click()

    // Practice identity, every field empty and typed for the first time.
    await expect(
      page.getByRole("heading", { name: "How insurers identify your practice" }),
    ).toBeVisible()
    await expect(page.getByLabel("Legal business name")).toHaveValue("")
    await page.getByLabel("Legal business name").fill("New Practice LLC")

    // The tax ID is write-only: it can be set or replaced, never cleared, so
    // the emptying above cannot remove one a previous spec left behind. Reveal
    // the entry the way a therapist would when there is already one on file.
    const change = page.getByRole("button", { name: "Change" })
    if (await change.isVisible()) await change.click()
    await page.getByTestId("tax-id-input").fill("123456789")
    await page.getByRole("radio", { name: "EIN" }).click()
    await page.getByRole("button", { name: "Save" }).click()

    // Saved means saved: the last four come back, the number never does.
    await expect(page.getByText("Ends in 6789")).toBeVisible()
    await page.getByRole("button", { name: "Continue" }).click()

    // Billing contact, saved on its own.
    await expect(page.getByRole("heading", { name: "Where should insurers reach you?" })).toBeVisible()
    await page.getByLabel("Billing address").fill("2 New St")
    await page.getByLabel("City").fill("Savannah")
    await page.getByLabel("State").fill("GA")
    await page.getByLabel("ZIP code").fill("31401")
    await page.getByLabel("Billing email").fill("new@example.com")
    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByRole("button", { name: "Save" })).toBeHidden()
    await page.getByRole("button", { name: "Continue" }).click()

    await expect(page.getByRole("heading", { name: "What you charge" })).toBeVisible()
    await page.getByRole("button", { name: "Continue" }).click()

    await expect(page.getByRole("heading", { name: "You're set up to get paid" })).toBeVisible()
    await page.getByRole("button", { name: "Finish", exact: true }).click()
    await page.waitForURL(/\/dashboard\/billing$/)

    // The record, not the screen. Both halves landed, and saving the contact
    // did not blank the identity saved before it — the property the two-card
    // split rests on, and the one that would fail silently.
    const after = await api.get<BillingProfile>("/api/practice/billing-profile")
    expect(after.legal_name).toBe("New Practice LLC")
    expect(after.tax_id_last4).toBe("6789")
    expect(after.tax_id_type).toBe("ein")
    expect(after.city).toBe("Savannah")
    expect(after.contact_email).toBe("new@example.com")

    const preferences = await api.get<Preferences>("/api/users/me/preferences")
    expect(preferences.billing_setup_route).toBe("private_pay")
    expect(preferences.billing_setup_complete).toBe(true)
  })
})

test("setup resumes where a new therapist left off", async ({ browser }) => {
  await withNewPractice(browser, async (page) => {
    await page.goto("/dashboard/billing/setup")

    // Wait for the answer to land rather than racing the save.
    const remembered = page.waitForResponse(
      (response: Response) =>
        response.url().includes("/api/users/me/preferences") &&
        response.request().method() === "PUT" &&
        response.ok(),
    )
    await page.getByText("My clients pay me directly").click()
    await remembered

    // Leave, the way closing a tab does.
    await page.goto("/dashboard")
    await page.goto("/dashboard/billing/setup")

    // Back on the step she left, on the branch she chose — not at the fork.
    // That step is the NPI lookup now: it leads every route, so answering the
    // fork lands her there rather than on the first practice form.
    await expect(page.getByRole("heading", { name: "Let's start with your NPI" })).toBeVisible()
    await expect(page.getByText("How do you get paid today?")).toBeHidden()
  })
})
