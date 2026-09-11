// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Booking link types
 *
 * Mirrors backend/app/models/booking_link.py. A link books exactly one
 * appointment type; length and the name shown on the row come from that
 * type, and `bookable` says whether the type's switches and the practice
 * policy currently let a new client book through it.
 */

export const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{2,63}$/

export interface BookingLink {
  id: string
  slug: string
  host_name: string
  title: string
  description: string | null
  appointment_type_id: string
  /** Resolved from the type; null once the type has been deleted. */
  appointment_type_name: string | null
  duration_minutes: number | null
  bookable: boolean
  /** Plain-language reason shown to the owner when `bookable` is false. */
  not_bookable_reason: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface BookingLinkListResponse {
  data: BookingLink[]
  total: number
}

export interface CreateBookingLinkRequest {
  slug: string
  host_name: string
  title: string
  description?: string
  appointment_type_id: string
}

export interface UpdateBookingLinkRequest {
  host_name?: string
  title?: string
  description?: string
  appointment_type_id?: string
  is_active?: boolean
}
