// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Booking Links API Functions
 *
 * Type-safe wrappers for the owner-facing booking-link management endpoints.
 */

import type {
  BookingLink,
  BookingLinkListResponse,
  CreateBookingLinkRequest,
  UpdateBookingLinkRequest,
} from "@/types/bookingLinks"
import { del, get, patch, post } from "./client"

export async function listBookingLinks(token?: string): Promise<BookingLinkListResponse> {
  return get<BookingLinkListResponse>("/api/booking-links", token)
}

export async function createBookingLink(
  data: CreateBookingLinkRequest,
  token?: string
): Promise<BookingLink> {
  return post<BookingLink>("/api/booking-links", data, token)
}

export async function updateBookingLink(
  linkId: string,
  data: UpdateBookingLinkRequest,
  token?: string
): Promise<BookingLink> {
  return patch<BookingLink>(`/api/booking-links/${linkId}`, data, token)
}

export async function deleteBookingLink(linkId: string, token?: string): Promise<void> {
  return del<void>(`/api/booking-links/${linkId}`, token)
}
