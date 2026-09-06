// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { getSchedulingPolicy, updateSchedulingPolicy } from "@/lib/api/schedulingPolicy"
import { queryKeys } from "@/lib/api/queryKeys"
import type { SchedulingPolicyResponse, UpdateSchedulingPolicyRequest } from "@/types/scheduling"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/**
 * The practice's scheduling policy: notice, cancel/reschedule cutoffs, the
 * new-patient flow and the two self-booking switches. A practice that has
 * never opened the settings has no row and reads back the strict defaults.
 */
export function useSchedulingPolicy(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.schedulingPolicy.detail(),
    queryFn: (): Promise<SchedulingPolicyResponse> => getSchedulingPolicy(token),
    staleTime: 60 * 1000,
  })
}

export function useUpdateSchedulingPolicy(token?: string) {
  return useAuthMutation({
    mutationFn: (data: UpdateSchedulingPolicyRequest) => updateSchedulingPolicy(data, token),
    invalidateKeys: [queryKeys.schedulingPolicy.all],
  })
}
