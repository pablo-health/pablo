// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient-side client for homework: what the practice asked the client to
 * do between visits, and marking it done.
 *
 * Written to the same shape as `patientRefills.ts`: a bare `fetch` carrying
 * the patient session token, because the clinician API client resolves its
 * credential from a signed-in clinician and the portal has none.
 *
 * Nothing here takes a patient id. The routes derive it from the token.
 * Failures carry a status and nothing else: a task or a note to the
 * clinician is health information.
 */

import { buildApiUrl } from "@/lib/api/client"

const BASE = "/api/patient/homework"

/** Server-side limit, mirrored so the input stops where the route would reject. */
export const CLIENT_NOTE_MAX = 500

export type PatientHomeworkStatus = "assigned" | "done"

export interface PatientHomework {
  id: string
  text: string
  status: PatientHomeworkStatus
  assigned_at: string | null
  completed_at: string | null
  client_note: string | null
}

export interface PatientHomeworkList {
  data: PatientHomework[]
  total: number
}

/** A failed request. Carries the status and no part of the payload. */
export class PatientHomeworkError extends Error {
  constructor(public status: number) {
    super(`Patient homework request failed (${status})`)
    this.name = "PatientHomeworkError"
  }
}

async function request<T>(sessionToken: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildApiUrl(`${BASE}${path}`), {
    ...init,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionToken}` },
  })
  if (!response.ok) throw new PatientHomeworkError(response.status)
  return (await response.json()) as T
}

/** The client's homework, open first. */
export function listHomework(sessionToken: string): Promise<PatientHomeworkList> {
  return request<PatientHomeworkList>(sessionToken, "")
}

/** Mark one done, with an optional note for the clinician. */
export function markHomeworkDone(
  sessionToken: string,
  id: string,
  note: string | null,
): Promise<PatientHomework> {
  return request<PatientHomework>(sessionToken, `/${id}/done`, {
    method: "POST",
    body: JSON.stringify({ note }),
  })
}

/** Not done after all. */
export function markHomeworkNotDone(sessionToken: string, id: string): Promise<PatientHomework> {
  return request<PatientHomework>(sessionToken, `/${id}/undo`, { method: "POST" })
}
