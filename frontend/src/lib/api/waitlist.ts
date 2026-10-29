// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Waitlist API client
 *
 * `GET    /api/waitlist`                      — the list, top first,
 * `PUT    /api/waitlist/order`                — put the entries in this order (409: the list changed),
 * `PATCH  /api/waitlist/{id}`                 — change what an entry wants, pause it, switch auto-offer,
 * `DELETE /api/waitlist/{id}`                 — take one off (returns it as it was),
 * `GET    /api/patients/{patient_id}/waitlist` — one client's entry, and defaults for adding them,
 * `POST   /api/patients/{patient_id}/waitlist` — put them on the list (409: not active, or already on it).
 */

import type {
  AddWaitlistEntryRequest,
  PatientWaitlist,
  UpdateWaitlistEntryRequest,
  WaitlistEntry,
  WaitlistResponse,
} from "@/types/waitlist"
import { del, get, patch, post, put } from "./client"

const LIST = "/api/waitlist"
const forPatient = (patientId: string) => `/api/patients/${patientId}/waitlist`

export function getWaitlist(token?: string): Promise<WaitlistResponse> {
  return get<WaitlistResponse>(LIST, token)
}

export function reorderWaitlist(entryIds: string[], token?: string): Promise<WaitlistResponse> {
  return put<WaitlistResponse>(`${LIST}/order`, { entry_ids: entryIds }, token)
}

export function updateWaitlistEntry(
  entryId: string,
  data: UpdateWaitlistEntryRequest,
  token?: string,
): Promise<WaitlistEntry> {
  return patch<WaitlistEntry>(`${LIST}/${entryId}`, data, token)
}

export function removeWaitlistEntry(entryId: string, token?: string): Promise<WaitlistEntry> {
  return del<WaitlistEntry>(`${LIST}/${entryId}`, token)
}

export function getPatientWaitlist(patientId: string, token?: string): Promise<PatientWaitlist> {
  return get<PatientWaitlist>(forPatient(patientId), token)
}

export function addToWaitlist(
  patientId: string,
  data: AddWaitlistEntryRequest,
  token?: string,
): Promise<WaitlistEntry> {
  return post<WaitlistEntry>(forPatient(patientId), data, token)
}
