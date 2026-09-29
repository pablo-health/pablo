// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Whether the practice offers its clients the portal — against
 * `backend/app/portal/settings_routes.py`.
 */

import { get, put } from "./client"

export interface PortalSettings {
  enabled: boolean
  /** False only for a practice that has never answered. */
  decided: boolean
}

const SETTINGS = "/api/portal/settings"

export function getPortalSettings(token?: string): Promise<PortalSettings> {
  return get<PortalSettings>(SETTINGS, token)
}

export function savePortalSettings(
  settings: Pick<PortalSettings, "enabled">,
  token?: string,
): Promise<PortalSettings> {
  return put<PortalSettings>(SETTINGS, settings, token)
}
