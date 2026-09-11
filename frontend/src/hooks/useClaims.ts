// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  acknowledgeRemittanceHold,
  buildClaimFromSession,
  checkClaimStatus,
  correctClaim,
  fetchClaim,
  listClaims,
  listRemittanceHolds,
  resolveRemittanceHold,
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
  RemittanceHold,
  RemittanceHoldFinding,
  RemittanceHoldListResponse,
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

/**
 * Ask the clearinghouse about one claim now. The answer is the detail view's
 * own shape, so it is written straight into the cache the detail reads rather
 * than invalidated and fetched again.
 */
export function useCheckClaimStatus(token?: string) {
  return useAuthMutation<ClaimDetailResponse, { claimId: string }>({
    mutationFn: ({ claimId }) => checkClaimStatus(claimId, token),
    onSuccess: (claim, _variables, queryClient) => {
      queryClient.setQueryData(queryKeys.claims.detail(claim.id), claim)
    },
    invalidateKeys: [queryKeys.claims.lists()],
  })
}

/** Remittances whose own numbers disagreed, so a client bill is waiting. */
export function useRemittanceHolds(token?: string) {
  return useAuthQuery<RemittanceHoldListResponse>({
    queryKey: queryKeys.claims.holds(),
    queryFn: () => listRemittanceHolds(token),
  })
}

/**
 * Bill the client what the payer stated, or waive it.
 *
 * Invalidates the billing keys as well as the claims ones: billing it as
 * stated writes a row on the client's ledger, and a balance shown stale
 * after the therapist just decided it is the one number they will check.
 */
export function useResolveRemittanceHold(token?: string) {
  return useAuthMutation<RemittanceHold, { holdId: string; finding: RemittanceHoldFinding }>({
    mutationFn: ({ holdId, finding }) => resolveRemittanceHold(holdId, finding, token),
    invalidateKeys: [queryKeys.claims.all, queryKeys.billing.all],
  })
}

/** Say it has been seen. Nothing is billed and the hold stays open. */
export function useAcknowledgeRemittanceHold(token?: string) {
  return useAuthMutation<RemittanceHold, { holdId: string }>({
    mutationFn: ({ holdId }) => acknowledgeRemittanceHold(holdId, token),
    invalidateKeys: [queryKeys.claims.holds()],
  })
}
