// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A client gives a time back, and that time is offered once through a
 * single-slot link.
 *
 * Two halves of one journey, both of which only a real stack can show:
 *
 * 1. The client cancels from the portal, and the open-slot event the engine
 *    raises for a client's own cancellation is recorded in the clinician's
 *    activity trail — by appointment id, naming nobody.
 * 2. The clinician makes a link for exactly that time. A stranger books it,
 *    and the next visitor is told the time has been taken rather than being
 *    offered a form that would refuse them.
 *
 * The practice is put in `request` mode (its default) so the booking lands as
 * a held request: what a practice that reviews self-bookings sees.
 *
 * Design: docs/design/public-booking.md (single-slot links).
 */

import type { Browser, BrowserContext, Page } from "@playwright/test"
import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import {
  givePortalContactDetails,
  givePortalInvitation,
  openPortalSection,
  signInToPortal,
} from "../fixtures/portal"
import { giveSelfBookableType, letExistingClientsSelfBook } from "../fixtures/portal-appointments"
import { giveBookableType, givePatient, letNewClientsSelfBook } from "../fixtures/scenarios"
import { BACKEND_URL, BASE_URL } from "../fixtures/stack"

interface CreatedAppointment {
  id: string
  start_at: string
}

interface SlotLink {
  slug: string
  slot_start_at: string
}

interface AuditRow {
  action: string
  resource_id: string
  actor_type: string
  patient_id: string | null
}

/** A browser with nothing saved in it: the stranger holding the link. */
async function anonymousPage(browser: Browser): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({ baseURL: BASE_URL, storageState: undefined })
  return { context, page: await context.newPage() }
}

/** The open-slot rows in the clinician's own activity trail. */
async function openSlotRows(api: ApiClient): Promise<AuditRow[]> {
  const trail = await api.get<{ data: AuditRow[] }>("/api/users/me/audit-log?limit=200")
  return trail.data.filter((row) => row.action === "appointment_slot_opened")
}

test.describe("Open slot and single-slot link", () => {
  test("a client cancels, the slot is recorded as open, and a link books it once", async ({
    api,
    page,
    browser,
    request,
  }) => {
    // --- the practice lets its clients manage their own appointments --------
    await giveSelfBookableType(api, `Therapy session ${Date.now().toString(36)}`)
    await letExistingClientsSelfBook(api)
    await api.patch("/api/scheduling/policy", { self_book_mode: "request" })

    // --- a client with an appointment a week out ---------------------------
    // Far enough ahead that the notice period does not apply, so the cancel
    // goes straight through.
    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(api, { email, phone, date_of_birth: "1990-06-14" })
    const startAt = new Date()
    startAt.setUTCDate(startAt.getUTCDate() + 7)
    startAt.setUTCHours(15, 0, 0, 0)
    const appointment = await api.post<CreatedAppointment>("/api/appointments", {
      patient_id: patient.id,
      title: "Session",
      start_at: startAt.toISOString(),
      end_at: new Date(startAt.getTime() + 50 * 60 * 1000).toISOString(),
      duration_minutes: 50,
      session_type: "individual",
    })

    // --- the client cancels from the portal --------------------------------
    await signInToPortal(page, await givePortalInvitation(api, patient.id, email, phone))
    await openPortalSection(page, "appointments")
    await page.getByTestId("appointments-row-cancel").click()
    await expect(page.getByTestId("appointments-none-upcoming")).toBeVisible()

    // --- and the open slot is on the clinician's record, by id alone -------
    await expect
      .poll(async () => (await openSlotRows(api)).map((row) => row.resource_id))
      .toContain(appointment.id)
    const [recorded] = (await openSlotRows(api)).filter((row) => row.resource_id === appointment.id)
    expect(recorded.actor_type).toBe("system")
    expect(recorded.patient_id).toBeNull()

    // --- the clinician offers exactly that time through a single-slot link -
    const newClientType = await giveBookableType(api)
    await letNewClientsSelfBook(api)
    const link = await api.post<SlotLink>("/api/booking-links/slot", {
      appointment_type_id: newClientType.id,
      start_at: appointment.start_at,
      host_name: "Wren Calloway",
      title: "Earlier time",
    })
    expect(new Date(link.slot_start_at).getTime()).toBe(new Date(appointment.start_at).getTime())

    // --- a stranger books it -----------------------------------------------
    const first = await anonymousPage(browser)
    try {
      await first.page.goto(`/book/${link.slug}`)
      await expect(first.page.getByRole("heading", { name: "Earlier time" })).toBeVisible()
      await expect(first.page.getByTestId("single-slot-time")).toBeVisible()
      await expect(first.page.getByText("Pick a day")).toHaveCount(0)

      await first.page.getByLabel("First name").fill("Rowan")
      await first.page.getByLabel("Last name").fill("Ellery")
      await first.page.getByLabel("Email").fill(`rowan.${Date.now().toString(36)}@example.com`)
      await first.page.getByRole("button", { name: "Confirm booking" }).click()

      await expect(first.page.getByRole("heading", { name: "Request sent" })).toBeVisible()
    } finally {
      await first.context.close()
    }

    // --- the next visitor is told it is gone -------------------------------
    const second = await anonymousPage(browser)
    try {
      await second.page.goto(`/book/${link.slug}`)
      await expect(
        second.page.getByRole("heading", { name: "This time has been taken" }),
      ).toBeVisible()
      await expect(second.page.getByLabel("First name")).toHaveCount(0)
    } finally {
      await second.context.close()
    }

    // And the server refuses a second booking even when asked directly.
    const refused = await request.post(
      `${BACKEND_URL}/api/public/booking-links/${link.slug}/bookings`,
      {
        data: {
          first_name: "Sasha",
          last_name: "Peralta",
          email: "sasha.peralta@example.com",
          start_at: link.slot_start_at,
          note: null,
        },
      },
    )
    expect(refused.status()).toBe(409)
    expect((await refused.json()).error.code).toBe("SLOT_TAKEN")
  })
})
