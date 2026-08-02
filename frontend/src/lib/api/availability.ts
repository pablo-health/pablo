// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Availability Rules API Functions
 *
 * Type-safe wrappers for the therapist availability-rule endpoints.
 */

import type {
  AvailabilityRule,
  AvailabilityRuleListResponse,
  CreateAvailabilityRuleRequest,
  UpdateAvailabilityRuleRequest,
} from "@/types/availability"
import { del, get, patch, post } from "./client"

export async function listAvailabilityRules(
  token?: string
): Promise<AvailabilityRuleListResponse> {
  return get<AvailabilityRuleListResponse>("/api/availability/rules", token)
}

export async function createAvailabilityRule(
  data: CreateAvailabilityRuleRequest,
  token?: string
): Promise<AvailabilityRule> {
  return post<AvailabilityRule>("/api/availability/rules", data, token)
}

export async function updateAvailabilityRule(
  ruleId: string,
  data: UpdateAvailabilityRuleRequest,
  token?: string
): Promise<AvailabilityRule> {
  return patch<AvailabilityRule>(`/api/availability/rules/${ruleId}`, data, token)
}

export async function deleteAvailabilityRule(
  ruleId: string,
  token?: string
): Promise<void> {
  return del<void>(`/api/availability/rules/${ruleId}`, token)
}
