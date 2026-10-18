// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Practice admins API client. All three calls are the practice owner's alone;
 * anyone else gets a 403.
 *
 * `GET /api/practice/admins` — every clinician of the practice,
 * `PUT /api/practice/admins/{user_id}` — make one an admin,
 * `DELETE /api/practice/admins/{user_id}` — take that away.
 */

import { del, get, put } from "./client"

export interface PracticeClinician {
  user_id: string
  name: string | null
  email: string
  is_owner: boolean
  /** True for the owner too: the owner may always change practice settings. */
  is_admin: boolean
}

export interface PracticeAdminsResponse {
  clinicians: PracticeClinician[]
}

export async function fetchPracticeAdmins(token?: string): Promise<PracticeAdminsResponse> {
  return get<PracticeAdminsResponse>("/api/practice/admins", token)
}

export async function grantPracticeAdmin(
  userId: string,
  token?: string,
): Promise<PracticeAdminsResponse> {
  return put<PracticeAdminsResponse>(`/api/practice/admins/${userId}`, {}, token)
}

export async function revokePracticeAdmin(
  userId: string,
  token?: string,
): Promise<PracticeAdminsResponse> {
  return del<PracticeAdminsResponse>(`/api/practice/admins/${userId}`, token)
}
