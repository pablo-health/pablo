// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Companion device + launch-intent API functions.
 *
 * Powers the desktop-handoff flow: the dashboard lists the user's enrolled
 * companion installs to decide whether to show "Start Session" (hand off to
 * the companion via a domain-verified deep link) or "Download Pablo
 * Companion". See docs/design/companion-thin-client.md.
 *
 * No PHI crosses either endpoint here — `/me/devices` returns only the
 * caller's own non-PHI device metadata, and `/launch/intent` returns an
 * opaque single-use token, never patient data.
 */

import { get, post } from "./client"

export type CompanionPlatform = "mac" | "windows" | "linux"

export interface CompanionDevice {
  install_id: string
  platform: CompanionPlatform
  os_version: string | null
  enrolled_at: string
  last_seen: string
  /** First 12 chars of the RFC 7638 key thumbprint — a non-secret recognizer. */
  jkt_fingerprint: string | null
}

export interface LaunchIntentResponse {
  intent_id: string
  /** `https://<host>/launch/<intent_id>` — host derived from backend APP_URL. */
  launch_url: string
  /** Seconds until the intent expires (always 180 per the handoff contract). */
  expires_in: number
}

/**
 * List the current user's enrolled (non-revoked) companion installs.
 *
 * Returns an empty array when the user has no companion enrolled. May 404
 * on deployments where the backend endpoint is not yet available — callers
 * should treat any failure as "no devices" and degrade gracefully.
 */
export async function listCompanionDevices(
  token?: string,
): Promise<CompanionDevice[]> {
  return get<CompanionDevice[]>("/api/users/me/devices", token)
}

/**
 * Issue a single-use launch intent for an appointment. The returned
 * `launch_url` is the domain-verified deep link the dashboard navigates to
 * in order to hand off to the companion. 404 when the backend launch flow
 * is flag-gated off.
 */
export async function createLaunchIntent(
  appointmentId: string,
  token?: string,
): Promise<LaunchIntentResponse> {
  return post<LaunchIntentResponse>(
    "/api/launch/intent",
    { appointment_id: appointmentId },
    token,
  )
}
