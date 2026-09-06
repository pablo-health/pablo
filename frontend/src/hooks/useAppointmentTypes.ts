// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  createAppointmentType,
  deleteAppointmentType,
  listAppointmentTypes,
  updateAppointmentType,
} from "@/lib/api/appointmentTypes"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  AppointmentTypeListResponse,
  CreateAppointmentTypeRequest,
  UpdateAppointmentTypeRequest,
} from "@/types/scheduling"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export function useAppointmentTypes(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.appointmentTypes.list(),
    queryFn: (): Promise<AppointmentTypeListResponse> => listAppointmentTypes(token),
    staleTime: 60 * 1000,
  })
}

export function useCreateAppointmentType(token?: string) {
  return useAuthMutation({
    mutationFn: (data: CreateAppointmentTypeRequest) => createAppointmentType(data, token),
    invalidateKeys: [queryKeys.appointmentTypes.all],
  })
}

export function useUpdateAppointmentType(token?: string) {
  return useAuthMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateAppointmentTypeRequest }) =>
      updateAppointmentType(id, data, token),
    invalidateKeys: [queryKeys.appointmentTypes.all],
  })
}

export function useDeleteAppointmentType(token?: string) {
  return useAuthMutation({
    mutationFn: (id: string) => deleteAppointmentType(id, token),
    invalidateKeys: [queryKeys.appointmentTypes.all],
  })
}
