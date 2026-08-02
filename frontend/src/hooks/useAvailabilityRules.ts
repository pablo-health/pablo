// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  AvailabilityRuleListResponse,
  AvailabilityRuleResponse,
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
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export function useAvailabilityRules(token?: string) {
  return useAuthQuery<AvailabilityRuleListResponse>({
    queryKey: queryKeys.availabilityRules.list(),
    queryFn: () => listAvailabilityRules(token),
  })
}

export function useCreateAvailabilityRule(token?: string) {
  return useAuthMutation<AvailabilityRuleResponse, CreateAvailabilityRuleRequest>({
    mutationFn: (data) => createAvailabilityRule(data, token),
    invalidateKeys: () => [queryKeys.availabilityRules.all],
  })
}

export function useUpdateAvailabilityRule(token?: string) {
  return useAuthMutation<
    AvailabilityRuleResponse,
    { ruleId: string; data: UpdateAvailabilityRuleRequest }
  >({
    mutationFn: ({ ruleId, data }) => updateAvailabilityRule(ruleId, data, token),
    invalidateKeys: () => [queryKeys.availabilityRules.all],
  })
}

export function useDeleteAvailabilityRule(token?: string) {
  return useAuthMutation<void, { ruleId: string }>({
    mutationFn: ({ ruleId }) => deleteAvailabilityRule(ruleId, token),
    invalidateKeys: () => [queryKeys.availabilityRules.all],
  })
}
