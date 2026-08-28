// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  BookingLink,
  CreateBookingLinkRequest,
  UpdateBookingLinkRequest,
} from "@/types/bookingLinks"
import {
  createBookingLink,
  deleteBookingLink,
  listBookingLinks,
  updateBookingLink,
} from "@/lib/api/bookingLinks"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthQuery, useAuthMutation } from "./useAuthQuery"

export function useBookingLinks(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.bookingLinks.list(),
    queryFn: () => listBookingLinks(token),
    staleTime: 60 * 1000,
  })
}

export function useCreateBookingLink(token?: string) {
  return useAuthMutation<BookingLink, CreateBookingLinkRequest>({
    mutationFn: (data) => createBookingLink(data, token),
    invalidateKeys: [queryKeys.bookingLinks.all],
  })
}

export function useUpdateBookingLink(token?: string) {
  return useAuthMutation<BookingLink, { linkId: string; data: UpdateBookingLinkRequest }>({
    mutationFn: ({ linkId, data }) => updateBookingLink(linkId, data, token),
    invalidateKeys: [queryKeys.bookingLinks.all],
  })
}

export function useDeleteBookingLink(token?: string) {
  return useAuthMutation<void, string>({
    mutationFn: (linkId) => deleteBookingLink(linkId, token),
    invalidateKeys: [queryKeys.bookingLinks.all],
  })
}
