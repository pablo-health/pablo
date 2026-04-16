// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  AppointmentResponse,
  CreateAppointmentRequest,
  UpdateAppointmentRequest,
} from "@/types/scheduling"
import {
  cancelAppointment,
  createAppointment,
  listAppointments,
  updateAppointment,
} from "@/lib/api/scheduling"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthQuery, useAuthMutation } from "./useAuthQuery"

export function useAppointmentList(start: string, end: string, token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.appointments.list({ start, end }),
    queryFn: () => listAppointments(start, end, token),
    staleTime: 60 * 1000,
    enabled: !!start && !!end,
  })
}

export function useCreateAppointment(token?: string) {
  return useAuthMutation({
    mutationFn: (data: CreateAppointmentRequest) => createAppointment(data, token),
    invalidateKeys: [queryKeys.appointments.all],
  })
}

export function useUpdateAppointment(token?: string) {
  return useAuthMutation<
    AppointmentResponse,
    { appointmentId: string; data: UpdateAppointmentRequest },
    AppointmentResponse
  >({
    mutationFn: ({ appointmentId, data }) =>
      updateAppointment(appointmentId, data, token),
    invalidateKeys: [queryKeys.appointments.all],
    optimistic: {
      queryKey: ({ appointmentId }) =>
        queryKeys.appointments.detail(appointmentId),
      updater: (previous, { data }) => ({ ...previous, ...data }),
    },
  })
}

export function useCancelAppointment(token?: string) {
  return useAuthMutation({
    mutationFn: (appointmentId: string) => cancelAppointment(appointmentId, token),
    invalidateKeys: [queryKeys.appointments.all],
  })
}
