// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Whether the practice offers its clients the portal, and which parts of it
 * — against `backend/app/portal/settings_routes.py`.
 */

import { get, put } from "./client"

export interface PortalSettings {
  enabled: boolean
  /** False only for a practice that has never answered. */
  decided: boolean
  /** Every part the practice can choose, in the order clients meet them. */
  modules: Record<string, boolean>
}

export interface PortalSettingsChange {
  enabled?: boolean
  /** Parts to turn on or off; ones left out keep their setting. */
  modules?: Record<string, boolean>
}

const SETTINGS = "/api/portal/settings"

export function getPortalSettings(token?: string): Promise<PortalSettings> {
  return get<PortalSettings>(SETTINGS, token)
}

export function savePortalSettings(
  change: PortalSettingsChange,
  token?: string,
): Promise<PortalSettings> {
  return put<PortalSettings>(SETTINGS, change, token)
}
