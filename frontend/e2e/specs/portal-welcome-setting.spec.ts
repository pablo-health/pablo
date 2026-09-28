// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice words the welcome its clients see in the portal, and a client of
 * that practice gets exactly that text.
 *
 * What only the real stack can prove: the settings card saves through the
 * real route into the real table, the text survives a reload, and a portal
 * session minted by the real invitation flow reads it back from the
 * capability document with the practice's name filled in — then gets the
 * default again once the practice goes back to it.
 *
 * Deliberately not here: the placeholder and length refusals and the
 * two-practice isolation, which cost milliseconds in
 * backend/tests/test_portal_welcome.py and test_portal_account_routes.py.
 */

import type { APIRequestContext } from "@playwright/test"
import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { givePortalContactDetails, givePortalSession } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

const CAPABILITIES_PATH = "/api/patient/capabilities"

const DEFAULT_HEADING = "Welcome to {practice_name}"

const CUSTOM = {
  heading: "Hello from {practice_name}",
  body: "We're glad you're here.\nFill in <b>your forms</b> before we meet.",
}

interface Capabilities {
  practice: { display_name: string | null }
  welcome: { heading: string; body: string }
}

async function useTheDefaultWelcome(api: ApiClient): Promise<void> {
  await api.delete("/api/portal/welcome")
}

async function readCapabilities(
  request: APIRequestContext,
  sessionToken: string,
): Promise<Capabilities> {
  const response = await request.get(`${BACKEND_URL}${CAPABILITIES_PATH}`, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  })
  expect(response.status(), "the capability document is served").toBe(200)
  return (await response.json()) as Capabilities
}

test.describe("The portal welcome", () => {
  test.beforeEach(async ({ api }) => {
    // The welcome is the practice's, shared by every spec in this worker.
    await useTheDefaultWelcome(api)
  })

  test.afterEach(async ({ api }) => {
    await useTheDefaultWelcome(api)
  })

  test("a practice words its welcome and its client reads exactly that @portal", async ({
    api,
    request,
    signedInPage: page,
  }) => {
    // --- the default, in settings ---------------------------------------
    await page.goto("/dashboard/settings/portal")
    const card = page.getByTestId("portal-welcome-card")
    await expect(card).toBeVisible()
    await expect(card.getByLabel("Heading")).toHaveValue(DEFAULT_HEADING)
    await expect(card.getByRole("button", { name: "Use the default" })).toHaveCount(0)

    // The name the portal fills in, as the settings route reports it.
    const { practice_name: practiceName } = await api.get<{ practice_name: string }>(
      "/api/portal/welcome",
    )
    const preview = page.getByTestId("portal-welcome-card-preview")
    await expect(preview).toContainText(`Welcome to ${practiceName}`)

    // --- the practice's own words ---------------------------------------
    await card.getByLabel("Heading").fill(CUSTOM.heading)
    await card.getByLabel("Message").fill(CUSTOM.body)
    // Text, not markup.
    await expect(preview).toContainText("<b>your forms</b>")
    await expect(preview.locator("b")).toHaveCount(0)

    await card.getByRole("button", { name: "Save", exact: true }).click()
    await expect(card.getByText("Saved", { exact: true })).toBeVisible()

    await page.reload()
    await expect(card.getByLabel("Heading")).toHaveValue(CUSTOM.heading)
    await expect(card.getByLabel("Message")).toHaveValue(CUSTOM.body)

    // --- a client of this practice reads it -----------------------------
    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(api, { email, phone })
    const sessionToken = await givePortalSession(api, request, patient.id, email, phone)

    const custom = await readCapabilities(request, sessionToken)
    expect(custom.practice.display_name).toBe(practiceName)
    expect(custom.welcome).toEqual({
      heading: `Hello from ${practiceName}`,
      body: CUSTOM.body,
    })

    // --- back to the default --------------------------------------------
    await card.getByRole("button", { name: "Use the default" }).click()
    await expect(card.getByLabel("Heading")).toHaveValue(DEFAULT_HEADING)
    await page.reload()
    await expect(card.getByLabel("Heading")).toHaveValue(DEFAULT_HEADING)

    const standard = await readCapabilities(request, sessionToken)
    expect(standard.welcome.heading).toBe(`Welcome to ${practiceName}`)
    expect(standard.welcome.body).toContain(`what ${practiceName} has asked you to do`)
  })
})
