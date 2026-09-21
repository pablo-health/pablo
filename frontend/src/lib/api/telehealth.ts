// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The video services a clinician can hold a session on, and connecting one.
 *
 * `listTelehealthProviders` answers what this deployment offers AND which of
 * those this clinician has connected, in one document, because those are two
 * different questions and a screen that only knew the first would offer a
 * room that never arrives.
 */

import { del, get, put } from "@/lib/api/client"

/** One video service, as settings and the appointment form show it. */
export interface TelehealthProvider {
  id: string
  display_name: string
  /** Whether a room can be produced right now, not merely offered. */
  connected: boolean
}

export interface TelehealthProviders {
  providers: TelehealthProvider[]
  default_provider: string | null
  room_url: string | null
  join_window_before_minutes: number
}

export interface ZoomStatus {
  connected: boolean
  account_handle: string | null
}

export async function listTelehealthProviders(): Promise<TelehealthProviders> {
  return get<TelehealthProviders>("/api/telehealth/providers")
}

/** Save your own permanent room. Pass null to remove it. */
export async function setTelehealthRoomUrl(
  roomUrl: string | null,
): Promise<{ room_url: string | null }> {
  return put<{ room_url: string | null }>("/api/telehealth/room-url", { room_url: roomUrl })
}

export async function getZoomStatus(): Promise<ZoomStatus> {
  return get<ZoomStatus>("/api/telehealth/zoom/status")
}

/**
 * Where to send the clinician to approve a Zoom connection.
 *
 * The redirect URI is sent rather than assumed, because the deployment checks
 * it against its own list before a code can be spent against it.
 */
export async function getZoomAuthUrl(redirectUri: string): Promise<{ auth_url: string }> {
  const query = new URLSearchParams({ redirect_uri: redirectUri })
  return get<{ auth_url: string }>(`/api/telehealth/zoom/authorize?${query.toString()}`)
}

/**
 * Spend the authorization code Zoom sent the browser back with.
 *
 * The other half of {@link getZoomAuthUrl}, and the reason the connect flow
 * finishes rather than dropping the clinician back on settings still
 * disconnected. `redirectUri` has to be the one the authorization URL was
 * built with: the deployment checks it again here, and Zoom checks that the
 * two agree.
 *
 * The code is single use. Calling this twice with the same one gets the
 * second attempt refused, so the caller is responsible for spending it once.
 */
export async function completeZoomConnect(
  code: string,
  state: string,
  redirectUri: string,
): Promise<ZoomStatus> {
  const query = new URLSearchParams({ code, state, redirect_uri: redirectUri })
  return get<ZoomStatus>(`/api/telehealth/zoom/callback?${query.toString()}`)
}

export async function disconnectZoom(): Promise<ZoomStatus> {
  return del<ZoomStatus>("/api/telehealth/zoom/disconnect")
}
