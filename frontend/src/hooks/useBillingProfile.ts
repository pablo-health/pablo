// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { getBillingProfile, updateBillingProfile } from "@/lib/api/practiceBilling"
import { queryKeys } from "@/lib/api/queryKeys"
import type { BillingProfileResponse, UpdateBillingProfileRequest } from "@/types/practiceBilling"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** The practice's billing identity. A practice that has never saved one
 * reads back all-null fields with the switches at their defaults. */
export function useBillingProfile(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.billingProfile.detail(),
    queryFn: (): Promise<BillingProfileResponse> => getBillingProfile(token),
    staleTime: 60 * 1000,
  })
}

export function useUpdateBillingProfile(token?: string) {
  return useAuthMutation({
    mutationFn: (data: UpdateBillingProfileRequest) => updateBillingProfile(data, token),
    invalidateKeys: [queryKeys.billingProfile.all],
  })
}
