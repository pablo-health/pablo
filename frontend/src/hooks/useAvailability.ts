// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  AvailabilityRule,
  CreateAvailabilityRuleRequest,
  UpdateAvailabilityRuleRequest,
} from "@/types/availability"
import {
  createAvailabilityRule,
  deleteAvailabilityRule,
  listAvailabilityRules,
  updateAvailabilityRule,
} from "@/lib/api/availability"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthQuery, useAuthMutation } from "./useAuthQuery"

export function useAvailabilityRules(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.availability.rules(),
    queryFn: () => listAvailabilityRules(token),
    staleTime: 60 * 1000,
  })
}

export function useCreateAvailabilityRule(token?: string) {
  return useAuthMutation<AvailabilityRule, CreateAvailabilityRuleRequest>({
    mutationFn: (data) => createAvailabilityRule(data, token),
    invalidateKeys: [queryKeys.availability.all],
  })
}

export function useUpdateAvailabilityRule(token?: string) {
  return useAuthMutation<
    AvailabilityRule,
    { ruleId: string; data: UpdateAvailabilityRuleRequest }
  >({
    mutationFn: ({ ruleId, data }) => updateAvailabilityRule(ruleId, data, token),
    invalidateKeys: [queryKeys.availability.all],
  })
}

export function useDeleteAvailabilityRule(token?: string) {
  return useAuthMutation<void, string>({
    mutationFn: (ruleId) => deleteAvailabilityRule(ruleId, token),
    invalidateKeys: [queryKeys.availability.all],
  })
}
