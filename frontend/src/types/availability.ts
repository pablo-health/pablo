// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Availability rule types
 *
 * Mirrors backend/app/scheduling_engine/models/availability.py — the
 * eight RuleType values and the two EnforcementLevel values. params
 * shape varies per rule_type; see AvailabilitySettings.tsx for the
 * per-type param forms.
 */

export const RULE_TYPES = [
  "working_hours",
  "block_day_of_week",
  "block_time_range",
  "max_per_day",
  "buffer_before",
  "buffer_after",
  "block_date_range",
  "block_specific_dates",
] as const

export type RuleType = (typeof RULE_TYPES)[number]

export const ENFORCEMENT_LEVELS = ["hard", "soft"] as const
export type EnforcementLevel = (typeof ENFORCEMENT_LEVELS)[number]

export interface AvailabilityRule {
  id: string
  user_id: string
  rule_type: RuleType
  enforcement: EnforcementLevel
  params: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
}

export interface AvailabilityRuleListResponse {
  data: AvailabilityRule[]
  total: number
}

export interface CreateAvailabilityRuleRequest {
  rule_type: RuleType
  enforcement: EnforcementLevel
  params: Record<string, unknown>
}

export interface UpdateAvailabilityRuleRequest {
  rule_type?: RuleType
  enforcement?: EnforcementLevel
  params?: Record<string, unknown>
}
