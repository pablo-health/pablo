// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The first client a practice adds asks whether it offers a client portal.
 *
 * Over the real stack, in a practice that has never answered (see
 * fixtures/freshPractice.ts): adding a client stops to ask, the answer is
 * saved as the practice's setting — read back through the API, not taken
 * from the screen — and the next client added is never asked again.
 *
 * One practice per path, because each answer is given once: two tests
 * sharing a practice would race for the one unanswered question. For the
 * same reason these tests do not retry — a retry would meet a practice that
 * has already answered, and fail for a reason that says nothing about the
 * first attempt.
 */

import { expect, test } from "../fixtures/auth"
import type { Page } from "@playwright/test"
import type { ApiClient } from "../fixtures/api"
import { signInToFreshPractice } from "../fixtures/freshPractice"
import { givePortalContactDetails } from "../fixtures/portal"

test.describe.configure({ retries: 0 })

interface PortalSettings {
  enabled: boolean
  decided: boolean
  modules: Record<string, boolean>
}

let sequence = 0

/** Add a client through the real dialog, stopping where the dialog runs on. */
async function addClient(page: Page, firstName: string): Promise<void> {
  const { email, phone } = givePortalContactDetails()
  await page.goto("/dashboard/patients")
  await page.getByRole("button", { name: /add client/i }).click()
  await page.getByLabel(/first name/i).fill(firstName)
  await page.getByLabel(/last name/i).fill(`Fresh${Date.now().toString(36)}${sequence++}`)
  await page.getByLabel(/email/i).fill(email)
  await page.getByLabel(/phone/i).fill(phone)
  await page.getByRole("button", { name: /create client/i }).click()
}

async function settingsOf(api: ApiClient): Promise<PortalSettings> {
  return api.get<PortalSettings>("/api/portal/settings")
}

test("saying not now is remembered, and the next client is not asked again @portal", async ({
  browser,
}) => {
  const { page, context, api } = await signInToFreshPractice(browser, "no")
  try {
    expect(await settingsOf(api)).toMatchObject({ enabled: false, decided: false })

    await addClient(page, "Avery")
    const prompt = page.getByTestId("portal-offer-prompt")
    await expect(prompt).toContainText("Offer your clients a portal?")
    await prompt.getByRole("button", { name: "Not now" }).click()

    const next = page.getByTestId("new-client-next-step")
    await expect(next).toContainText("What should Avery do next?")
    await expect(next).toContainText("You can turn on the client portal any time in Settings.")
    // No invitation to a portal the practice just chose not to offer.
    await expect(next.getByRole("checkbox", { name: "Invite them to the portal" })).toHaveCount(0)
    expect(await settingsOf(api)).toMatchObject({ enabled: false, decided: true })

    await next.getByRole("button", { name: "Not now" }).click()
    await addClient(page, "Blake")
    await expect(page.getByTestId("new-client-next-step")).toContainText("What should Blake do next?")
    await expect(page.getByTestId("portal-offer-prompt")).toHaveCount(0)
  } finally {
    await context.close()
  }
})

test("saying yes turns the portal on with the parts chosen, and invitations follow @portal", async ({
  browser,
}) => {
  const { page, context, api } = await signInToFreshPractice(browser, "yes")
  try {
    expect(await settingsOf(api)).toMatchObject({ enabled: false, decided: false })

    await addClient(page, "Casey")
    await page.getByTestId("portal-offer-prompt").getByRole("button", { name: "Yes, set it up" }).click()

    const choose = page.getByTestId("portal-offer-choose")
    await expect(choose).toContainText("What can clients do in it?")
    // Everything this stack serves starts on.
    for (const part of ["Forms", "Messages", "Appointments", "Refill requests"]) {
      await expect(choose.getByRole("checkbox", { name: part })).toBeChecked()
    }
    await choose.getByRole("checkbox", { name: "Appointments" }).click()
    await choose.getByRole("button", { name: "Turn on the portal" }).click()

    const next = page.getByTestId("new-client-next-step")
    await expect(next).toContainText("What should Casey do next?")
    // The portal is on, so the next step offers the invitation.
    await expect(next.getByRole("checkbox", { name: "Invite them to the portal" })).toBeVisible()

    const saved = await settingsOf(api)
    expect(saved).toMatchObject({ enabled: true, decided: true })
    expect(saved.modules).toMatchObject({
      intake: true,
      messaging: true,
      appointments: false,
      refills: true,
    })

    await next.getByRole("button", { name: "Not now" }).click()
    await addClient(page, "Drew")
    await expect(page.getByTestId("new-client-next-step")).toContainText("What should Drew do next?")
    await expect(page.getByTestId("portal-offer-prompt")).toHaveCount(0)
  } finally {
    await context.close()
  }
})
