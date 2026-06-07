// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  addSupervisionHours,
  createSupervisionRelationship,
  deleteSupervisionRelationship,
  listSupervisionHours,
  listSupervisionRelationships,
  updateSupervisionRelationship,
} from "@/lib/api/supervision"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  SupervisionRelationshipPayload,
  SupervisionHoursPayload,
} from "@/types/supervision"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export function useSupervisionRelationships(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.supervision.list(),
    queryFn: () => listSupervisionRelationships(token),
  })
}

export function useCreateSupervisionRelationship(token?: string) {
  return useAuthMutation({
    mutationFn: (payload: SupervisionRelationshipPayload) =>
      createSupervisionRelationship(payload, token),
    invalidateKeys: [queryKeys.supervision.list()],
  })
}

export function useUpdateSupervisionRelationship(token?: string) {
  return useAuthMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string
      payload: SupervisionRelationshipPayload
    }) => updateSupervisionRelationship(id, payload, token),
    invalidateKeys: [queryKeys.supervision.list()],
  })
}

export function useDeleteSupervisionRelationship(token?: string) {
  return useAuthMutation({
    mutationFn: (id: string) => deleteSupervisionRelationship(id, token),
    invalidateKeys: [queryKeys.supervision.list()],
  })
}

export function useSupervisionHours(id: string, token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.supervision.hours(id),
    queryFn: () => listSupervisionHours(id, token),
    enabled: !!id,
  })
}

export function useAddSupervisionHours(id: string, token?: string) {
  return useAuthMutation({
    mutationFn: (payload: SupervisionHoursPayload) =>
      addSupervisionHours(id, payload, token),
    invalidateKeys: [queryKeys.supervision.hours(id)],
  })
}
