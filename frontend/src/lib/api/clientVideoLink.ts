// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * `GET`/`PUT /api/users/me/practice/client-video-link` — whether the
 * practice's messages to clients carry a video appointment's link.
 */

import { get, put } from "./client"

export interface ClientVideoLinkSetting {
  send_clients_video_link: boolean
  /** False when the deployment sends no link to anybody; the setting then changes nothing. */
  available: boolean
  /** Whether the caller may change the setting (the practice owner). */
  can_change: boolean
}

const PATH = "/api/users/me/practice/client-video-link"

/** Readable by every clinician in the practice. */
export function fetchClientVideoLinkSetting(token?: string): Promise<ClientVideoLinkSetting> {
  return get<ClientVideoLinkSetting>(PATH, token)
}

/** The practice owner only; anyone else gets a 403. */
export function updateClientVideoLinkSetting(
  send: boolean,
  token?: string,
): Promise<ClientVideoLinkSetting> {
  return put<ClientVideoLinkSetting>(PATH, { send_clients_video_link: send }, token)
}
