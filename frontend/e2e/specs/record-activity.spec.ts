// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Record activity: the practice owner sees who acted on a client's record —
 * including the client, through the portal.
 *
 * Rows a client writes are recorded with the client as the actor, and until
 * the owner-read policy no clinician could read them at all. So the claim
 * under test is a property of the whole path: the portal writes the row as
 * the client, the database lets the owner (and only the owner) read it, and
 * the chart says it in plain words. A mocked client could prove none of that.
 *
 * The owner is this worker's clinician, which is the first account to sign
 * into the practice (see `workerClinician` in fixtures/auth.ts). The
 * non-owner is a second account in the same practice.
 */

import { expect, test } from "../fixtures/auth"
import { ApiClient, ApiError, ensureEmulatorUser } from "../fixtures/api"
import {
  givePortalContactDetails,
  givePortalInvitation,
  openPortalSection,
  signInToPortal,
} from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BASE_URL } from "../fixtures/stack"

test.use({ bypassCSP: true })

const NON_OWNER = {
  email: "e2e-record-activity-colleague@example.com",
  password: "E2e-record-activity-colleague-password",
}

test.describe("record activity", () => {
  test("the owner sees what the client did in the portal @portal", async ({
    api,
    browser,
    signedInPage,
  }) => {
    const status = await api.get<{ can_view_record_activity?: boolean }>("/api/users/me/status")
    expect(status.can_view_record_activity, "this worker's clinician owns the practice").toBe(
      true,
    )

    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(api, { email, phone })
    const invitation = await givePortalInvitation(api, patient.id, email, phone)

    // The client, in a browser of their own: sign in, then send a message.
    const clientContext = await browser.newContext({ baseURL: BASE_URL, storageState: undefined })
    try {
      const portal = await clientContext.newPage()
      await signInToPortal(portal, invitation)
      await openPortalSection(portal, "messaging")
      await portal.getByTestId("portal-messaging-start-thread").click()
      await portal.getByTestId("portal-messaging-new-thread-subject").fill("A question")
      await portal.getByTestId("portal-messaging-new-thread-body").fill("Can we talk Thursday?")
      await portal.getByTestId("portal-messaging-new-thread-send").click()
      await expect(portal.getByTestId("portal-messaging-thread-view")).toBeVisible()
    } finally {
      await clientContext.close()
    }

    await signedInPage.goto(`/dashboard/patients/${patient.id}`)
    await signedInPage.getByRole("button", { name: "Record activity" }).click()

    const list = signedInPage.getByRole("list", { name: "Record activity" })
    await expect(list.getByText("Client signed in to the portal")).toBeVisible()
    await expect(list.getByText("Client sent a message")).toBeVisible()
    await expect(list.getByText("You viewed the chart").first()).toBeVisible()

    // Actions only: the message itself never reaches this panel.
    await expect(list.getByText("Can we talk Thursday?")).toHaveCount(0)

    // Reading the trail is itself recorded, as the owner's own row.
    const page = await api.get<{ data: Array<{ action: string; is_you: boolean }> }>(
      `/api/patients/${patient.id}/activity`,
    )
    expect(page.data.some((row) => row.action === "patient_activity_viewed" && row.is_you)).toBe(
      true,
    )
  })

  test("a clinician who does not own the practice has no link and no access", async ({
    api,
    browser,
  }) => {
    // The owner signs in first (the `api` fixture), so this account joins
    // the practice as a second clinician rather than claiming it.
    const ownersPatient = await givePatient(api)
    await ensureEmulatorUser(NON_OWNER.email, NON_OWNER.password)
    const colleague = await ApiClient.forUser(NON_OWNER.email, NON_OWNER.password)

    const status = await colleague.get<{ can_view_record_activity?: boolean }>(
      "/api/users/me/status",
    )
    expect(status.can_view_record_activity).toBe(false)

    const ownPatient = await givePatient(colleague)
    for (const patientId of [ownPatient.id, ownersPatient.id]) {
      const refused = await colleague
        .get(`/api/patients/${patientId}/activity`)
        .then(() => null, (error: unknown) => error)
      expect(refused).toBeInstanceOf(ApiError)
      expect((refused as ApiError).status).toBe(403)
    }

    const context = await browser.newContext({ baseURL: BASE_URL, storageState: undefined })
    try {
      const page = await context.newPage()
      await page.goto("/login")
      await page.getByLabel("Email").fill(NON_OWNER.email)
      await page.getByLabel("Password", { exact: true }).fill(NON_OWNER.password)
      await page.getByRole("button", { name: "Sign In", exact: true }).click()
      await page.waitForURL(/\/dashboard/)

      await page.goto(`/dashboard/patients/${ownPatient.id}`)
      await expect(page.getByText(ownPatient.first_name).first()).toBeVisible()
      await expect(page.getByRole("button", { name: "Record activity" })).toHaveCount(0)
    } finally {
      await context.close()
    }
  })
})
