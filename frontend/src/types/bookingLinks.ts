// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Booking link types
 *
 * Mirrors backend/app/models/booking_link.py — the three SESSION_TYPES
 * values and the slug pattern the server validates against.
 */

export const SESSION_TYPES = ["individual", "couples", "group"] as const
export type SessionType = (typeof SESSION_TYPES)[number]

export const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{2,63}$/

export interface BookingLink {
  id: string
  slug: string
  host_name: string
  title: string
  description: string | null
  duration_minutes: number
  session_type: string
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
  duration_minutes: number
  session_type: string
}

export interface UpdateBookingLinkRequest {
  host_name?: string
  title?: string
  description?: string
  duration_minutes?: number
  is_active?: boolean
}
