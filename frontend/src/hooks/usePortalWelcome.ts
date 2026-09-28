// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  getPortalWelcome,
  resetPortalWelcome,
  savePortalWelcome,
  type PortalWelcome,
  type PortalWelcomeDraft,
} from "@/lib/api/portalWelcome"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export const portalWelcomeKeys = {
  all: ["portalWelcome"] as const,
}

/** The practice's portal welcome. `retry: false`: a deployment without the
 * portal answers 404, and asking again does not change that. */
export function usePortalWelcome() {
  return useAuthQuery<PortalWelcome>({
    queryKey: portalWelcomeKeys.all,
    queryFn: () => getPortalWelcome(),
    retry: false,
  })
}

export function useSavePortalWelcome() {
  return useAuthMutation<PortalWelcome, PortalWelcomeDraft>({
    mutationFn: (draft) => savePortalWelcome(draft),
    invalidateKeys: [portalWelcomeKeys.all],
  })
}

export function useResetPortalWelcome() {
  return useAuthMutation<PortalWelcome, void>({
    mutationFn: () => resetPortalWelcome(),
    invalidateKeys: [portalWelcomeKeys.all],
  })
}
