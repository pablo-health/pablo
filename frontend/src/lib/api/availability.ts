// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Availability Rules API Functions
 *
 * Type-safe wrappers for the therapist availability-rule endpoints.
 */

import type {
  AvailabilityRule,
  AvailabilityRuleListResponse,
  CheckConflictsRequest,
  CheckConflictsResponse,
  CreateAvailabilityRuleRequest,
  FreeSlotsResponse,
  ParseAvailabilityRulesRequest,
  ParseAvailabilityRulesResponse,
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

export async function getFreeSlots(
  date: string,
  duration?: number,
  token?: string
): Promise<FreeSlotsResponse> {
  const params = new URLSearchParams({ date })
  if (duration) params.set("duration", String(duration))
  return get<FreeSlotsResponse>(`/api/availability/slots?${params}`, token)
}

export async function parseAvailabilityRules(
  data: ParseAvailabilityRulesRequest,
  token?: string
): Promise<ParseAvailabilityRulesResponse> {
  return post<ParseAvailabilityRulesResponse>("/api/availability/rules/parse", data, token)
}

/**
 * Check a proposed booking window against the clinician's availability
 * rules. The authoritative answer is the one the write path gives, so this
 * is asked once at save time rather than as the therapist types.
 */
export async function checkConflicts(
  data: CheckConflictsRequest,
  token?: string
): Promise<CheckConflictsResponse> {
  return post<CheckConflictsResponse>("/api/availability/check", data, token)
}
