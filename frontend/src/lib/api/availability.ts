// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Availability rule API client
 *
 * Type-safe wrappers around the availability rule endpoints:
 * `GET /api/availability/rules` — list,
 * `POST /api/availability/rules` — create,
 * `PATCH /api/availability/rules/{id}` — update,
 * `DELETE /api/availability/rules/{id}` — remove.
 */

import type {
  AvailabilityRuleListResponse,
  AvailabilityRuleResponse,
  CreateAvailabilityRuleRequest,
  UpdateAvailabilityRuleRequest,
} from "@/types/availability"
import { del, get, patch, post } from "./client"

export async function listAvailabilityRules(
  token?: string,
): Promise<AvailabilityRuleListResponse> {
  return get<AvailabilityRuleListResponse>("/api/availability/rules", token)
}

export async function createAvailabilityRule(
  data: CreateAvailabilityRuleRequest,
  token?: string,
): Promise<AvailabilityRuleResponse> {
  return post<AvailabilityRuleResponse>("/api/availability/rules", data, token)
}

export async function updateAvailabilityRule(
  ruleId: string,
  data: UpdateAvailabilityRuleRequest,
  token?: string,
): Promise<AvailabilityRuleResponse> {
  return patch<AvailabilityRuleResponse>(
    `/api/availability/rules/${ruleId}`,
    data,
    token,
  )
}

export async function deleteAvailabilityRule(
  ruleId: string,
  token?: string,
): Promise<void> {
  return del<void>(`/api/availability/rules/${ruleId}`, token)
}
