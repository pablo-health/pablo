// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { BookSessionCard } from "../website/BookSessionCard"
import type { BookingButton } from "@/lib/api/bookingButton"
import type { BookingLink } from "@/types/bookingLinks"

const mutate = vi.fn()
let saved: BookingButton
let links: BookingLink[]

vi.mock("@/hooks/useBookingButton", () => ({
  useBookingButton: () => ({ data: saved }),
  useSetBookingButton: () => ({ mutate, isPending: false, error: null }),
}))

vi.mock("@/hooks/useBookingLinks", () => ({
  useBookingLinks: () => ({ data: { data: links, total: links.length } }),
}))

const OFF: BookingButton = {
  booking_link_id: null,
  outside_url: null,
  on_website: false,
  in_portal: false,
  website_url: null,
  portal_url: null,
  website_available: true,
}

function link(overrides: Partial<BookingLink> = {}): BookingLink {
  return {
    id: "link_1",
    slug: "first-session",
    host_name: "Sam Rivera",
    title: "First session",
    description: null,
    appointment_type_id: "type_1",
    appointment_type_name: "Intake",
    duration_minutes: 50,
    bookable: true,
    not_bookable_reason: null,
    is_active: true,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  saved = OFF
  links = [link()]
})

describe("BookSessionCard", () => {
  it("is off, with nothing to turn on until a booking page is chosen", () => {
    render(<BookSessionCard canManage showWebsite />)

    expect(screen.getByRole("switch", { name: "On your website" })).toHaveProperty("disabled", true)
    expect(screen.getByRole("switch", { name: "In your client portal" })).toHaveProperty("disabled", true)
  })

  it("saves a booking page used elsewhere, shown in the portal", async () => {
    const user = userEvent.setup()
    saved = { ...OFF, outside_url: "https://book.example.net/old" }
    render(<BookSessionCard canManage showWebsite />)

    const address = screen.getByLabelText("Booking page address")
    await user.clear(address)
    await user.type(address, "https://book.example.net/riverside")
    await user.click(screen.getByRole("switch", { name: "In your client portal" }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mutate).toHaveBeenCalledWith(
      {
        booking_link_id: null,
        outside_url: "https://book.example.net/riverside",
        on_website: false,
        in_portal: true,
      },
      expect.anything(),
    )
  })

  it("says the button is hidden while the chosen booking link is off", () => {
    links = [link({ is_active: false })]
    saved = { ...OFF, booking_link_id: "link_1", on_website: true }
    render(<BookSessionCard canManage showWebsite />)

    expect(screen.getByTestId("book-session-link-off").textContent).toBe(
      "This booking link is off, so the button is hidden.",
    )
  })

  it("leaves the website out where this deployment cannot show the link there", () => {
    saved = { ...OFF, website_available: false }
    render(<BookSessionCard canManage showWebsite />)

    expect(screen.queryByRole("switch", { name: "On your website" })).toBeNull()
    expect(screen.getByRole("switch", { name: "In your client portal" })).toBeTruthy()
  })

  it("leaves the website out where websites are not published, and is read-only for others", () => {
    render(<BookSessionCard canManage={false} showWebsite={false} />)

    expect(screen.queryByRole("switch", { name: "On your website" })).toBeNull()
    expect(screen.getByRole("button", { name: "Save" })).toHaveProperty("disabled", true)
  })
})
