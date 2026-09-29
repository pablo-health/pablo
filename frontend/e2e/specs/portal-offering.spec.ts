// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice decides whether it offers its clients the portal.
 *
 * What only the real stack can prove:
 *
 *   1. **Off locks out a client who is already signed in**, not just new
 *      ones — every door at once: the signed-in session, the practice's
 *      public address and new invitations. And back on, the same session
 *      carries on, because turning it off suspends access rather than
 *      revoking it.
 *   2. **A part turned off is gone for real**: the capability document drops
 *      it, and its routes — booking included, for appointments — answer 404
 *      to a client who is signed in.
 *   3. **The settings page shows the switches** a practice uses to do it.
 *
 * The off/on round trip runs on the SECOND practice. Every worker shares the
 * first one, and turning its portal off would pull it out from under every
 * other portal spec running at the same time. The confirmation dialog and
 * the greyed cards are component facts, covered by
 * PortalOfferingCard.test.tsx and PatientPortalPage.test.tsx.
 */

import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { ApiError } from "../fixtures/api"
import { givePortalContactDetails, givePortalSession } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

const SETTINGS = "/api/portal/settings"
const CAPABILITIES = `${BACKEND_URL}/api/patient/capabilities`

async function portalAddress(api: ApiClient): Promise<string> {
  const { slug } = await api.post<{ slug: string }>("/api/portal/practice-slug")
  return `${BACKEND_URL}/api/portal/practices/${slug}`
}

test.describe("offering the portal", () => {
  test("off locks out a signed-in client and the practice's address; back on, the same session works", async ({
    otherPracticeApi: api,
    request,
  }) => {
    await api.put(SETTINGS, { enabled: true })
    try {
      const { email, phone } = givePortalContactDetails()
      const patient = await givePatient(api, { email, phone })
      const session = await givePortalSession(api, request, patient.id, email, phone)
      const signedIn = { headers: { Authorization: `Bearer ${session}` } }
      const address = await portalAddress(api)

      expect((await request.get(CAPABILITIES, signedIn)).status()).toBe(200)
      expect((await request.get(address)).status()).toBe(200)

      expect(await api.put(SETTINGS, { enabled: false })).toMatchObject({
        enabled: false,
        decided: true,
      })

      expect((await request.get(CAPABILITIES, signedIn)).status()).toBe(401)
      expect((await request.get(address)).status()).toBe(404)
      const refused = await api
        .post(`/api/patients/${patient.id}/portal-invite`)
        .then(() => null, (error: unknown) => error)
      expect(refused).toBeInstanceOf(ApiError)
      expect((refused as ApiError).status).toBe(409)
      const access = await api.get<{ portal_enabled: boolean }>(
        `/api/patients/${patient.id}/portal-access`,
      )
      expect(access.portal_enabled).toBe(false)

      await api.put(SETTINGS, { enabled: true })
      expect((await request.get(CAPABILITIES, signedIn)).status()).toBe(200)
    } finally {
      await api.put(SETTINGS, { enabled: true })
    }
  })

  test("a part the practice turns off disappears from the portal and its routes refuse", async ({
    otherPracticeApi: api,
    request,
  }) => {
    await api.put(SETTINGS, { enabled: true })
    try {
      const { email, phone } = givePortalContactDetails()
      const patient = await givePatient(api, { email, phone })
      const session = await givePortalSession(api, request, patient.id, email, phone)
      const signedIn = { headers: { Authorization: `Bearer ${session}` } }
      const appointments = `${BACKEND_URL}/api/patient/appointments`

      expect((await request.get(appointments, signedIn)).status()).toBe(200)

      const saved = await api.put<{ modules: Record<string, boolean> }>(SETTINGS, {
        modules: { appointments: false },
      })
      expect(saved.modules.appointments).toBe(false)

      const capabilities = await (await request.get(CAPABILITIES, signedIn)).json()
      expect(capabilities.modules.appointments).toBe(false)
      expect(capabilities.modules.messaging).toBe(true)
      expect((await request.get(appointments, signedIn)).status()).toBe(404)
      expect(
        (await request.get(`${BACKEND_URL}/api/patient/booking/options`, signedIn)).status(),
      ).toBe(404)
    } finally {
      await api.put(SETTINGS, { enabled: true, modules: { appointments: true } })
    }
  })

  test("the settings page shows the practice's switch", async ({ signedInPage: page }) => {
    await page.goto("/dashboard/settings/portal")

    const toggle = page.getByRole("switch", { name: "Client portal" })
    await expect(toggle).toBeVisible()
    await expect(toggle).toHaveAttribute("aria-checked", "true")
    await expect(page.getByTestId("portal-off-note")).toHaveCount(0)
    // The parts this stack serves, each with its own switch, on by default.
    for (const part of ["Forms", "Messages", "Appointments", "Refill requests"]) {
      await expect(page.getByRole("switch", { name: part })).toHaveAttribute("aria-checked", "true")
    }
    await expect(page.getByTestId("portal-booking-line")).toContainText("Booking settings")
  })
})
