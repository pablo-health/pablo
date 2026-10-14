// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Record activity: who acted on a client's record, and when.
 *
 * For the practice owner only; anyone else gets 403. Actions only — the
 * server sends no content, no IP address and no browser.
 */

import { get } from "./client"

export type ActivityActorType = "clinician" | "staff" | "patient" | "system" | "anonymous"

export interface RecordActivityItem {
  id: string
  timestamp: string
  actor_type: ActivityActorType
  /** The clinician's or staff member's name; for a background job, the part
   * of the system that acted. Null for the client. */
  actor_name: string | null
  /** The row is the reader's own. */
  is_you: boolean
  action: string
  resource_type: string
}

export interface RecordActivityPage {
  data: RecordActivityItem[]
  limit: number
  /** Present when older rows may exist behind this page. */
  next_cursor: string | null
}

/** A page of a client's record activity, newest first. Reading it is recorded. */
export async function getRecordActivity(
  patientId: string,
  params: { cursor?: string | null; limit?: number } = {},
  token?: string
): Promise<RecordActivityPage> {
  const query = new URLSearchParams()
  if (params.limit) query.set("limit", String(params.limit))
  if (params.cursor) query.set("cursor", params.cursor)
  const suffix = query.size > 0 ? `?${query.toString()}` : ""
  return get<RecordActivityPage>(
    `/api/patients/${encodeURIComponent(patientId)}/activity${suffix}`,
    token
  )
}
