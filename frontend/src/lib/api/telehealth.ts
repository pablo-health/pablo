// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The video services a clinician can hold a session on, and connecting one.
 *
 * `listTelehealthProviders` answers what this deployment offers AND which of
 * those this clinician has connected, in one document, because those are two
 * different questions and a screen that only knew the first would offer a
 * room that never arrives.
 */

import { del, get, patch, put } from "@/lib/api/client"

/** One video service, as settings and the appointment form show it. */
export interface TelehealthProvider {
  id: string
  display_name: string
  /** Whether a room can be produced right now, not merely offered. */
  connected: boolean
}

export interface TelehealthProviders {
  providers: TelehealthProvider[]
  /** The default in use: null when none is saved or the saved one is not connected. */
  default_provider: string | null
  room_url: string | null
  /** Your own Zoom room, pasted once. */
  zoom_room_url: string | null
  /**
   * Whether this deployment has a Zoom app, so connecting Zoom makes a meeting
   * per appointment. Without one, Zoom means the room you paste.
   */
  zoom_app: boolean
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

// Kept character-for-character with the server's rule (is_zoom_room_url in
// backend/app/models/validators.py) and pinned by the case table both run, so
// the card never accepts what the server then refuses. A pattern rather than
// new URL(): the server can't use a browser's parser, and two parsers is how
// the two checks came to disagree. The host must follow https:// directly, so
// userinfo and ports never match, and a backslash (read as / by a browser)
// is refused anywhere.
const ZOOM_ROOM = /^https:\/\/([a-z0-9-]+\.)*zoom\.us\.?([/?#]|$)/i

/**
 * An https address on zoom.us or one of its subdomains, the same rule the
 * deployment applies. A look-alike host is refused: patients click this.
 */
export function isZoomRoomUrl(value: string): boolean {
  const trimmed = value.trim()
  return ZOOM_ROOM.test(trimmed) && !trimmed.includes("\\")
}

/**
 * Save your own Zoom room. Pass null to remove it.
 *
 * The deployment accepts only an https address on zoom.us and refuses
 * anything else as a field error.
 */
export async function setZoomRoomUrl(
  zoomRoomUrl: string | null,
): Promise<{ zoom_room_url: string | null }> {
  return put<{ zoom_room_url: string | null }>("/api/telehealth/zoom-room-url", {
    zoom_room_url: zoomRoomUrl,
  })
}

/**
 * Make one of your connected services the one sessions use when the
 * appointment does not say. Changes that preference and nothing else.
 */
export async function setDefaultVideoService(providerId: string): Promise<void> {
  await patch("/api/users/me/preferences", { default_video_platform: providerId })
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
