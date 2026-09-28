// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The portal opens on a home screen, with a page for each section.
 *
 * Driven through a browser against the whole stack, because what is under
 * test is the wiring a unit test cannot reach: the route under the shell,
 * the portal host's short addresses, browser history, and summaries read off
 * the real API.
 *
 *   1. The code lands the patient on Home: the practice's welcome and one
 *      tile per section, with no section's content drawn there.
 *   2. A tile says where its section stands, from the patient's own data.
 *   3. A tile opens its section on a page of its own; the navigation marks
 *      it; Back returns to Home.
 *   4. A section's address survives a reload with a live session.
 *   5. A section this practice does not serve has no tile and no link, and
 *      its address goes back to Home.
 *
 * **One sign-in for all of it.** Redeeming an invitation is rate limited per
 * caller, and every spec reaches the stack from the same address, so this
 * spends one redeem and walks the journeys in steps.
 *
 * This stack serves the portal on a host of its own, so the addresses here
 * are `/{slug}` and `/{slug}/{section}`. The assertions match on the end of
 * the address, which holds on either form.
 */

import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { defaultIntakeVersion } from "../fixtures/intake"
import {
  givePortalContactDetails,
  givePortalInvitation,
  signInToPortal,
} from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

const SECTIONS = ["forms", "messaging", "appointments", "refills"] as const

interface IntakeTemplate {
  id: string
  versions: { id: string }[]
}

/** A one-question form, published, so the patient has a second one to fill in. */
async function publishShortForm(api: ApiClient): Promise<string> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `Check-in ${Date.now().toString(36)}`,
  })
  const draftId = template.versions[0].id
  await api.put(`/api/intake/templates/${template.id}/versions/${draftId}/items`, {
    items: [
      { key: "anything", item_type: "free_text", label: "Anything else?", config: { max_len: 500 } },
    ],
  })
  const published = await api.post<{ id: string }>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
  return published.id
}

test("the portal opens on a home screen, with a page for each section @portal", async ({
  api,
  page,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })

  // Two forms waiting: the practice's seeded intake, and one more.
  for (const versionId of [await defaultIntakeVersion(api), await publishShortForm(api)]) {
    await api.post(`/api/patients/${patient.id}/intake-assignments`, { version_id: versionId })
  }
  const medication = await api.post<{ id: string }>(`/api/patients/${patient.id}/medications`, {
    drug_name: `E2E Sertraline ${Date.now().toString(36)}`,
    dose: "50 mg",
    status: "active",
  })

  const invitation = await givePortalInvitation(api, patient.id, email, phone)
  const slug = new URL(invitation.link).pathname.split("/")[2]
  const atHome = new RegExp(`/${slug}$`)
  const at = (section: string) => new RegExp(`/${slug}/${section}$`)

  await test.step("the code lands on Home: the welcome, and a tile per section", async () => {
    await signInToPortal(page, invitation)
    await expect(page).toHaveURL(atHome)
    await expect(page.getByTestId("portal-home")).toBeVisible()

    // The practice's default welcome, or — from a server that sends none —
    // the practice's name. Both open the same way.
    await expect(page.getByTestId("portal-home-welcome-heading")).toHaveText(/^Welcome to \S/)

    for (const section of SECTIONS) {
      await expect(page.getByTestId(`portal-home-tile-${section}`)).toBeVisible()
    }
    // Tiles only: no section draws its content on Home.
    await expect(page.locator("[data-testid^='portal-section-']")).toHaveCount(0)
    await expect(page.getByTestId("forms-list")).toHaveCount(0)
    await expect(page.getByTestId("portal-refills")).toHaveCount(0)
  })

  await test.step("a tile says where its section stands", async () => {
    await expect(page.getByTestId("portal-home-tile-forms-summary")).toHaveText(
      "2 forms to complete",
    )
    await expect(page.getByTestId("portal-home-tile-refills-summary")).toHaveText(
      "No refill requests",
    )

    // A refill asked for with this browser's own session, then approved by
    // the prescriber. Home, reloaded, says so and when.
    const stored = await page.evaluate(
      (key) => window.localStorage.getItem(key),
      `pablo-portal-session:${slug}`,
    )
    const { sessionToken } = JSON.parse(stored ?? "{}") as { sessionToken: string }
    const asked = await request.post(`${BACKEND_URL}/api/patient/refills`, {
      headers: { Authorization: `Bearer ${sessionToken}` },
      data: { medication_id: medication.id },
    })
    expect(asked.status(), "asking for a refill").toBe(201)
    const refill = (await asked.json()) as { id: string }
    await api.post(`/api/refill-requests/${refill.id}/decision`, {
      status: "approved",
      prescriber_note: "private to the practice",
    })

    await page.reload()
    await expect(page.getByTestId("portal-home-tile-refills-summary")).toHaveText(
      /^Sent to your pharmacy · \S+/,
    )
  })

  await test.step("a tile opens its section alone, and Back returns Home", async () => {
    await page.getByTestId("portal-home-tile-refills").click()
    await expect(page).toHaveURL(at("refills"))
    await expect(page.getByTestId("portal-section-refills")).toBeVisible()
    await expect(page.getByTestId("portal-refills")).toBeVisible()
    await expect(page.getByTestId("portal-home")).toHaveCount(0)
    await expect(page.getByTestId("forms-list")).toHaveCount(0)
    await expect(page.getByTestId("portal-shell-nav-refills")).toHaveAttribute(
      "aria-current",
      "page",
    )
    await expect(page.getByTestId("portal-shell-nav-home")).not.toHaveAttribute(
      "aria-current",
      "page",
    )

    await page.goBack()
    await expect(page).toHaveURL(atHome)
    await expect(page.getByTestId("portal-home")).toBeVisible()
    await expect(page.getByTestId("portal-shell-nav-home")).toHaveAttribute(
      "aria-current",
      "page",
    )
  })

  await test.step("a section's address survives a reload", async () => {
    await page.getByTestId("portal-shell-nav-messaging").click()
    await expect(page).toHaveURL(at("messaging"))
    await expect(page.getByTestId("portal-section-messaging")).toBeVisible()

    await page.reload()
    await expect(page.getByTestId("portal-section-messaging")).toBeVisible()
    await expect(page).toHaveURL(at("messaging"))
    await expect(page.getByTestId("portal-shell-otp")).toHaveCount(0)
    await expect(page.getByTestId("portal-shell-no-session")).toHaveCount(0)
  })

  await test.step("a section the practice does not serve is not offered", async () => {
    // Which modules exist is deployment configuration, and this stack serves
    // them all. So the one answer that says refills is off is written into
    // the capability document on its way to the browser; everything the
    // page does with it is the real thing.
    await page.route("**/api/patient/capabilities", async (route) => {
      const response = await route.fetch()
      const body = (await response.json()) as { modules: Record<string, boolean> }
      await route.fulfill({ response, json: { ...body, modules: { ...body.modules, refills: false } } })
    })
    const home = page.url().replace(/\/messaging$/, "")

    await page.goto(home)
    await expect(page.getByTestId("portal-home-tile-forms")).toBeVisible()
    await expect(page.getByTestId("portal-home-tile-refills")).toHaveCount(0)
    await expect(page.getByTestId("portal-shell-nav-refills")).toHaveCount(0)

    await page.goto(`${home}/refills`)
    await expect(page).toHaveURL(atHome)
    await expect(page.getByTestId("portal-home")).toBeVisible()
    await expect(page.getByTestId("portal-refills")).toHaveCount(0)
  })
})
