// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Supervision API
 *
 * Type-safe wrappers for /api/supervision — the per-clinician supervision
 * and delegation relationship surface.
 */

import type {
  SupervisionRelationship,
  SupervisionRelationshipPayload,
  SupervisionHoursEntry,
  SupervisionHoursPayload,
} from "@/types/supervision"
import { del, get, post, put } from "./client"

export async function listSupervisionRelationships(
  token?: string,
): Promise<SupervisionRelationship[]> {
  return get<SupervisionRelationship[]>("/api/supervision", token)
}

export async function createSupervisionRelationship(
  payload: SupervisionRelationshipPayload,
  token?: string,
): Promise<SupervisionRelationship> {
  return post<SupervisionRelationship>("/api/supervision", payload, token)
}

export async function updateSupervisionRelationship(
  id: string,
  payload: SupervisionRelationshipPayload,
  token?: string,
): Promise<SupervisionRelationship> {
  return put<SupervisionRelationship>(`/api/supervision/${id}`, payload, token)
}

export async function deleteSupervisionRelationship(
  id: string,
  token?: string,
): Promise<void> {
  return del<void>(`/api/supervision/${id}`, token)
}

export async function listSupervisionHours(
  id: string,
  token?: string,
): Promise<SupervisionHoursEntry[]> {
  return get<SupervisionHoursEntry[]>(`/api/supervision/${id}/hours`, token)
}

export async function addSupervisionHours(
  id: string,
  payload: SupervisionHoursPayload,
  token?: string,
): Promise<SupervisionHoursEntry> {
  return post<SupervisionHoursEntry>(
    `/api/supervision/${id}/hours`,
    payload,
    token,
  )
}
