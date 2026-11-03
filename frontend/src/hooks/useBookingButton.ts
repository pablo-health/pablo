// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { QueryClient } from "@tanstack/react-query"
import {
  type BookingButton,
  type BookingButtonSettings,
  getBookingButton,
  setBookingButton,
} from "@/lib/api/bookingButton"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export const bookingButtonKeys = {
  all: ["bookingButton"] as const,
}

export function useBookingButton() {
  return useAuthQuery<BookingButton>({
    queryKey: bookingButtonKeys.all,
    queryFn: () => getBookingButton(),
  })
}

export function useSetBookingButton() {
  return useAuthMutation<BookingButton, BookingButtonSettings>({
    mutationFn: (settings) => setBookingButton(settings),
    onSuccess: (data: BookingButton, _variables: unknown, queryClient: QueryClient) => {
      queryClient.setQueryData(bookingButtonKeys.all, data)
    },
  })
}
