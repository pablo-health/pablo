// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's side of refill requests (`app.routes.refill_requests`).
 *
 * `pending` is the queue — everything still waiting across the caller's
 * patients, oldest first. `recent` is the last answers given, newest first.
 * A decision is recorded once; answering a request somebody else already
 * answered is a 409.
 */

import { get, post } from "./client"

export type RefillQueueView = "pending" | "recent"

export type RefillStatus = "requested" | "approved" | "needs_visit" | "declined"

export type RefillDecision = Exclude<RefillStatus, "requested">

export interface ClinicianRefillRequest {
  id: string
  patient_id: string
  patient_name: string | null
  medication_id: string | null
  medication_text: string
  pharmacy_text: string | null
  patient_note: string | null
  status: RefillStatus
  created_at: string
  decided_at: string | null
  decided_by_user_id: string | null
  prescriber_note: string | null
}

export interface RefillQueueResponse {
  data: ClinicianRefillRequest[]
  total: number
}

export interface DecideRefillInput {
  requestId: string
  status: RefillDecision
  prescriberNote: string | null
}

export async function fetchRefillQueue(
  view: RefillQueueView,
  token?: string,
): Promise<RefillQueueResponse> {
  return get<RefillQueueResponse>(`/api/refill-requests?view=${view}`, token)
}

export async function decideRefillRequest(
  input: DecideRefillInput,
  token?: string,
): Promise<ClinicianRefillRequest> {
  return post<ClinicianRefillRequest>(
    `/api/refill-requests/${input.requestId}/decision`,
    { status: input.status, prescriber_note: input.prescriberNote },
    token,
  )
}
