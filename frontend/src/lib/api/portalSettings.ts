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
  /** The practice's own target for answering a refill request, in working days. */
  refill_turnaround_days: number
}

/** The range the backend accepts for `refill_turnaround_days`. */
export const REFILL_TURNAROUND_MIN_DAYS = 1
export const REFILL_TURNAROUND_MAX_DAYS = 10

export interface PortalSettingsChange {
  enabled?: boolean
  /** Parts to turn on or off; ones left out keep their setting. */
  modules?: Record<string, boolean>
  refill_turnaround_days?: number
  /** Apply only if the practice has never answered (the first-client prompt). */
  only_if_undecided?: boolean
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
