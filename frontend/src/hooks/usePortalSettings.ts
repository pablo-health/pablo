// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { getPortalSettings, savePortalSettings, type PortalSettings } from "@/lib/api/portalSettings"
import { portalAccessKeys } from "./usePortalAccess"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export const portalSettingsKeys = {
  all: ["portalSettings"] as const,
}

/** Whether the practice offers the portal. `retry: false`: a deployment
 * without the portal answers 404, and asking again does not change that. */
export function usePortalSettings() {
  return useAuthQuery<PortalSettings>({
    queryKey: portalSettingsKeys.all,
    queryFn: () => getPortalSettings(),
    retry: false,
  })
}

export function useSavePortalSettings() {
  return useAuthMutation<PortalSettings, Pick<PortalSettings, "enabled">>({
    mutationFn: (settings) => savePortalSettings(settings),
    // Every client's access state carries the switch too.
    invalidateKeys: [portalSettingsKeys.all, portalAccessKeys.all],
  })
}
