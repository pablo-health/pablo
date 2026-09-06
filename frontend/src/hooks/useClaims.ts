// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  buildClaimFromSession,
  correctClaim,
  fetchClaim,
  listClaims,
  validateClaim,
  voidClaim,
} from "@/lib/api/claims"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  BuildClaimRequest,
  ClaimDetailResponse,
  ClaimResponse,
  ClaimTrackerFilters,
  ClaimTrackerResponse,
  ValidateClaimResponse,
} from "@/types/claims"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** The tracker: every claim the clinician can see, newest first. */
export function useClaims(filters: ClaimTrackerFilters = {}, token?: string) {
  return useAuthQuery<ClaimTrackerResponse>({
    queryKey: queryKeys.claims.list(filters),
    queryFn: () => listClaims(filters, token),
  })
}

export function useClaim(claimId: string | undefined, token?: string) {
  return useAuthQuery<ClaimDetailResponse>({
    queryKey: queryKeys.claims.detail(claimId ?? ""),
    queryFn: () => fetchClaim(claimId!, token),
    enabled: !!claimId,
  })
}

/** Snapshot a visit into a draft claim. The queue row changes with it. */
export function useBuildClaim(token?: string) {
  return useAuthMutation<ClaimResponse, { appointmentId: string; data?: BuildClaimRequest }>({
    mutationFn: ({ appointmentId, data }) => buildClaimFromSession(appointmentId, data, token),
    invalidateKeys: [queryKeys.claims.all, queryKeys.billing.all],
  })
}

/** Run the scrub. A blocking finding rejects with `CLAIM_VALIDATION_FAILED`. */
export function useValidateClaim(token?: string) {
  return useAuthMutation<ValidateClaimResponse, { claimId: string }>({
    mutationFn: ({ claimId }) => validateClaim(claimId, token),
    invalidateKeys: ({ claimId }) => [
      queryKeys.claims.detail(claimId),
      queryKeys.claims.lists(),
      queryKeys.billing.all,
    ],
  })
}

export function useCorrectClaim(token?: string) {
  return useAuthMutation<ClaimResponse, { claimId: string }>({
    mutationFn: ({ claimId }) => correctClaim(claimId, token),
    invalidateKeys: [queryKeys.claims.all, queryKeys.billing.all],
  })
}

export function useVoidClaim(token?: string) {
  return useAuthMutation<ClaimResponse, { claimId: string }>({
    mutationFn: ({ claimId }) => voidClaim(claimId, token),
    invalidateKeys: [queryKeys.claims.all, queryKeys.billing.all],
  })
}
