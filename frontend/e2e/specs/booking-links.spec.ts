// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The clinician's side of a booking link: it books one appointment type, and
 * it only takes bookings once the type and the practice both say a stranger
 * may book that type.
 *
 * The public spec proves the happy path end to end. This one proves the
 * gates and the owner's view of them: every closed switch shows up as a
 * plain reason on the settings page and as a bare "not found" to anyone
 * holding the URL, and flipping the switches opens the link without
 * touching it.
 *
 * Design: docs/design/public-booking.md.
 */

import { test, expect } from "../fixtures/auth"
import {
  giveBookableType,
  giveBookingLink,
  letNewClientsSelfBook,
  type BookingLink,
} from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

const SETTINGS_URL = "/dashboard/settings/scheduling"

const PRACTICE_CLOSED = "Your practice does not let new clients book themselves."
const NOT_SELF_BOOKABLE = "This appointment type is not marked as bookable by clients."

test.describe("Booking link gates", () => {
  test("a link stays closed until its type and the practice both allow self-booking", async ({
    api,
    signedInPage: page,
    request,
  }) => {
    // Start with every switch off: a type nobody may self-book, and a
    // practice that has not opened self-booking to new clients.
    const type = await giveBookableType(api, { name: "Gated intake", self_bookable: false })
    await letNewClientsSelfBook(api, false)
    const link = await api.post<BookingLink>("/api/booking-links", {
      slug: `e2e-gated-${Date.now()}`,
      host_name: "Wren Calloway",
      title: "Gated intake",
      appointment_type_id: type.id,
    })
    expect(link.bookable).toBe(false)
    expect(link.not_bookable_reason).toContain(PRACTICE_CLOSED)

    // The owner sees the reason on the settings page.
    await page.goto(SETTINGS_URL)
    const row = page.getByRole("listitem").filter({ hasText: `/book/${link.slug}` })
    await expect(row).toBeVisible()
    await expect(row.getByRole("note")).toContainText(PRACTICE_CLOSED)
    await expect(row).toContainText(`50 min · ${type.name}`)

    // A stranger sees nothing at all: the same not-found an unknown slug gets.
    const closed = await request.get(`${BACKEND_URL}/api/public/booking-links/${link.slug}`)
    expect(closed.status()).toBe(404)
    const unknown = await request.get(`${BACKEND_URL}/api/public/booking-links/no-such-link`)
    expect(await closed.json()).toEqual(await unknown.json())

    // Open the practice switch: the type's own switch is now the reason.
    await letNewClientsSelfBook(api, true)
    const stillClosed = (await api.get<{ data: BookingLink[] }>("/api/booking-links")).data.find(
      (l) => l.id === link.id,
    )
    expect(stillClosed?.bookable).toBe(false)
    expect(stillClosed?.not_bookable_reason).toContain(NOT_SELF_BOOKABLE)
    expect((await request.get(`${BACKEND_URL}/api/public/booking-links/${link.slug}`)).status()).toBe(
      404,
    )

    // Open the type's switch: the link is live, and nothing on it changed.
    await api.patch(`/api/appointment-types/${type.id}`, { self_bookable: true })
    const open = (await api.get<{ data: BookingLink[] }>("/api/booking-links")).data.find(
      (l) => l.id === link.id,
    )
    expect(open?.bookable).toBe(true)
    expect(open?.not_bookable_reason).toBeNull()

    const card = await request.get(`${BACKEND_URL}/api/public/booking-links/${link.slug}`)
    expect(card.status()).toBe(200)
    expect(await card.json()).toMatchObject({ slug: link.slug, duration_minutes: 50 })

    await page.reload()
    const openRow = page.getByRole("listitem").filter({ hasText: `/book/${link.slug}` })
    await expect(openRow).toBeVisible()
    await expect(openRow.getByRole("note")).toHaveCount(0)
  })

  test("the settings form creates a link by picking an appointment type", async ({
    api,
    signedInPage: page,
  }) => {
    const type = await giveBookableType(api, { name: "Consultation call", duration_minutes: 20 })
    await letNewClientsSelfBook(api)
    const slug = `e2e-form-${Date.now()}`

    await page.goto(SETTINGS_URL)
    await page.getByRole("button", { name: "New booking link" }).click()
    await page.getByLabel("Slug").fill(slug)
    await page.getByLabel("Host name").fill("Wren Calloway")
    await page.getByLabel("Title").fill("Consultation")

    // The form asks for a type, never for a length: the type answers that.
    // The scheduling page carries its own "Appointment type" controls, so the
    // create form's picker is addressed by its id rather than its label.
    await expect(page.getByLabel("Length (minutes)")).toHaveCount(0)
    await page.locator("#link-appointment-type").click()
    await page.getByRole("option", { name: `${type.name} · 20 min` }).click()
    await page.getByRole("button", { name: "Create link" }).click()

    const row = page.getByRole("listitem").filter({ hasText: `/book/${slug}` })
    await expect(row).toBeVisible()
    await expect(row).toContainText(`20 min · ${type.name}`)
    await expect(row.getByRole("note")).toHaveCount(0)

    const created = (await api.get<{ data: BookingLink[] }>("/api/booking-links")).data.find(
      (l) => l.slug === slug,
    )
    expect(created?.appointment_type_id).toBe(type.id)
    expect(created?.bookable).toBe(true)
  })

  test("a link whose type is deleted is listed, closed, and says why", async ({
    api,
    signedInPage: page,
    request,
  }) => {
    const link = await giveBookingLink(api, { title: "Orphaned intake" })
    expect(link.bookable).toBe(true)

    await api.delete(`/api/appointment-types/${link.appointment_type_id}`)

    await page.goto(SETTINGS_URL)
    const row = page.getByRole("listitem").filter({ hasText: `/book/${link.slug}` })
    await expect(row).toContainText("Appointment type deleted")
    await expect(row.getByRole("note")).toContainText("no longer exists")

    expect((await request.get(`${BACKEND_URL}/api/public/booking-links/${link.slug}`)).status()).toBe(
      404,
    )
  })
})
