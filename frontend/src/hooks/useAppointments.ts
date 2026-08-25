// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  AppointmentListResponse,
  AppointmentResponse,
  CreateAppointmentRequest,
  CreateRecurringAppointmentRequest,
  EditSeriesRequest,
  UpdateAppointmentRequest,
} from "@/types/scheduling"
import {
  cancelAppointment,
  cancelAppointmentSeries,
  createAppointment,
  createRecurringAppointment,
  editAppointmentSeries,
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

/** Range params a list query was created with, read back off its query key. */
function listRangeOf(queryKey: readonly unknown[]): { start: string; end: string } | undefined {
  const params = queryKey[2]
  if (!params || typeof params !== "object") return undefined
  const { start, end } = params as { start?: unknown; end?: unknown }
  return typeof start === "string" && typeof end === "string" ? { start, end } : undefined
}

export function useCreateAppointment(token?: string) {
  return useAuthMutation<AppointmentResponse, CreateAppointmentRequest>({
    mutationFn: (data) => createAppointment(data, token),
    onSuccess: (appointment, _variables, queryClient) => {
      const startAt = new Date(appointment.start_at).getTime()
      const cachedLists = queryClient.getQueriesData<AppointmentListResponse>({
        queryKey: queryKeys.appointments.lists(),
      })
      for (const [queryKey, cached] of cachedLists) {
        const range = listRangeOf(queryKey)
        if (!cached || !range) continue
        const rangeStart = new Date(range.start).getTime()
        const rangeEnd = new Date(range.end).getTime()
        if (startAt < rangeStart || startAt >= rangeEnd) continue
        queryClient.setQueryData<AppointmentListResponse>(queryKey, {
          ...cached,
          data: [...cached.data, appointment],
          total: cached.total + 1,
        })
      }
    },
  })
}

export function useCreateRecurringAppointment(token?: string) {
  return useAuthMutation<AppointmentListResponse, CreateRecurringAppointmentRequest>({
    mutationFn: (data) => createRecurringAppointment(data, token),
    invalidateKeys: [queryKeys.appointments.lists()],
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

export function useEditAppointmentSeries(token?: string) {
  return useAuthMutation<
    AppointmentListResponse,
    { appointmentId: string; data: EditSeriesRequest }
  >({
    mutationFn: ({ appointmentId, data }) =>
      editAppointmentSeries(appointmentId, data, token),
    invalidateKeys: [queryKeys.appointments.all],
  })
}

export function useCancelAppointmentSeries(token?: string) {
  return useAuthMutation<AppointmentListResponse, string>({
    mutationFn: (appointmentId) => cancelAppointmentSeries(appointmentId, token),
    invalidateKeys: [queryKeys.appointments.all],
  })
}
