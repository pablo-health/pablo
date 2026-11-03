// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's "Book a session" link on its website and in its portal
 * (backend/app/portal/booking_button_routes.py).
 */

import { get, put } from "./client"

export interface BookingButtonSettings {
  booking_link_id: string | null
  outside_url: string | null
  on_website: boolean
  in_portal: boolean
}

export interface BookingButton extends BookingButtonSettings {
  /** Where the link on the website goes now; null when it shows nothing. */
  website_url: string | null
  /** Where the link in the portal goes now; null when it shows nothing. */
  portal_url: string | null
}

const PATH = "/api/practice/booking-button"

export function getBookingButton(): Promise<BookingButton> {
  return get<BookingButton>(PATH)
}

export function setBookingButton(settings: BookingButtonSettings): Promise<BookingButton> {
  return put<BookingButton>(PATH, settings)
}
