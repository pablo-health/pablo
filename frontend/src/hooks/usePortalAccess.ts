// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  getPortalAccess,
  issuePortalInvite,
  revokePortalAccess,
  type PortalAccessRevoked,
  type PortalAccessState,
  type PortalInviteAccepted,
} from "@/lib/api/portalAccess"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export const portalAccessKeys = {
  all: ["portalAccess"] as const,
  forPatient: (patientId: string) => [...portalAccessKeys.all, patientId] as const,
}

/**
 * Whether this patient can reach the portal, and whether an invite is waiting.
 *
 * `retry: false` because the informative answers here are the ones worth
 * showing at once: a deployment without the portal answers 404, and asking it
 * three times does not make it a different deployment.
 */
export function usePortalAccess(patientId: string | undefined, token?: string) {
  return useAuthQuery<PortalAccessState>({
    queryKey: portalAccessKeys.forPatient(patientId ?? ""),
    queryFn: () => getPortalAccess(patientId!, token),
    enabled: !!patientId,
    retry: false,
  })
}

/** Send this patient a way in. The credential goes to them, never to here. */
export function useIssuePortalInvite(patientId: string, token?: string) {
  return useAuthMutation<PortalInviteAccepted, void>({
    mutationFn: () => issuePortalInvite(patientId, token),
    invalidateKeys: [portalAccessKeys.forPatient(patientId)],
  })
}

/** Cut off every live session and burn any invite still unused. */
export function useRevokePortalAccess(patientId: string, token?: string) {
  return useAuthMutation<PortalAccessRevoked, void>({
    mutationFn: () => revokePortalAccess(patientId, token),
    invalidateKeys: [portalAccessKeys.forPatient(patientId)],
  })
}
