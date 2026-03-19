// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Appointment React Query Hooks
 *
 * Custom hooks for appointment CRUD with optimistic updates and cache invalidation.
 */

"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
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

/**
 * Fetch appointments for a date range.
 */
export function useAppointmentList(start: string, end: string, token?: string) {
  return useQuery({
    queryKey: queryKeys.appointments.list({ start, end }),
    queryFn: () => listAppointments(start, end, token),
    staleTime: 60 * 1000,
    enabled: !!start && !!end,
  })
}

/**
 * Create a new appointment with cache invalidation.
 */
export function useCreateAppointment(token?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: CreateAppointmentRequest) => createAppointment(data, token),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.appointments.all })
    },
  })
}

/**
 * Update an appointment with optimistic update.
 */
export function useUpdateAppointment(token?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      appointmentId,
      data,
    }: {
      appointmentId: string
      data: UpdateAppointmentRequest
    }) => updateAppointment(appointmentId, data, token),
    onMutate: async ({ appointmentId, data }) => {
      await queryClient.cancelQueries({
        queryKey: queryKeys.appointments.detail(appointmentId),
      })
      const previous = queryClient.getQueryData<AppointmentResponse>(
        queryKeys.appointments.detail(appointmentId)
      )
      if (previous) {
        queryClient.setQueryData<AppointmentResponse>(
          queryKeys.appointments.detail(appointmentId),
          { ...previous, ...data }
        )
      }
      return { previous }
    },
    onError: (_error, { appointmentId }, context) => {
      if (context?.previous) {
        queryClient.setQueryData(
          queryKeys.appointments.detail(appointmentId),
          context.previous
        )
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.appointments.all })
    },
  })
}

/**
 * Cancel an appointment with cache invalidation.
 */
export function useCancelAppointment(token?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (appointmentId: string) => cancelAppointment(appointmentId, token),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.appointments.all })
    },
  })
}
