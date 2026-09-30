// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  AppointmentResponse,
  GoogleChangeResolution,
  HeldGoogleRemovals,
} from "@/types/scheduling"
import {
  getHeldGoogleRemovals,
  resolveGoogleChange,
  resolveHeldGoogleRemovals,
} from "@/lib/api/scheduling"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** Sessions removed from Google Calendar in bulk and held for the therapist. */
export function useHeldGoogleRemovals() {
  return useAuthQuery({
    queryKey: queryKeys.appointments.heldGoogleRemovals(),
    queryFn: () => getHeldGoogleRemovals(),
    staleTime: 60 * 1000,
  })
}

export function useResolveGoogleChange() {
  return useAuthMutation<
    AppointmentResponse,
    { appointmentId: string; resolution: GoogleChangeResolution }
  >({
    mutationFn: ({ appointmentId, resolution }) =>
      resolveGoogleChange(appointmentId, resolution),
    invalidateKeys: [queryKeys.appointments.all],
  })
}

export function useResolveHeldGoogleRemovals() {
  return useAuthMutation<HeldGoogleRemovals, GoogleChangeResolution>({
    mutationFn: (resolution) => resolveHeldGoogleRemovals(resolution),
    invalidateKeys: [queryKeys.appointments.all],
  })
}
