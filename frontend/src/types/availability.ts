// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Availability rule types
 *
 * Mirrors backend/app/scheduling_engine/models/availability.py — the
 * ten RuleType values and the two EnforcementLevel values.
 *
 * params shape varies per rule_type, and the authority on it is the
 * backend's tagged union, emitted as availabilityRuleParams.schema.json
 * beside this file: the API rejects params that don't match their rule
 * type, including an unknown key. AvailabilitySettings.tsx holds the
 * per-type param forms and is pinned to that schema by
 * AvailabilityParamsContract.test.ts. session_defaults has its own
 * dedicated fields section rather than a generic RuleForm entry.
 */

export const RULE_TYPES = [
  "working_hours",
  "block_day_of_week",
  "block_time_range",
  "max_per_day",
  "max_per_week",
  "buffer_before",
  "buffer_after",
  "block_date_range",
  "block_specific_dates",
  "session_defaults",
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

export interface ParseAvailabilityRulesRequest {
  text: string
}

export interface ProposedAvailabilityRule {
  rule_type: RuleType
  enforcement: EnforcementLevel
  params: Record<string, unknown>
  human_summary: string
}

export interface ParseAvailabilityRulesResponse {
  proposals: ProposedAvailabilityRule[]
  could_not_parse: string | null
  exclusive: boolean
  existing_conflicting_rules: AvailabilityRule[]
}

/**
 * A single rule a proposed booking window runs into. Mirrors
 * ConflictResponse in backend/app/models/scheduling.py — rule type and
 * enforcement level plus the engine's message, with no rule params.
 */
export interface ConflictResponse {
  rule_type: RuleType
  enforcement: EnforcementLevel
  message: string
}

export interface CheckConflictsRequest {
  start_at: string
  end_at: string
}

export interface CheckConflictsResponse {
  conflicts: ConflictResponse[]
  has_hard_conflicts: boolean
  // False when the practice has no availability rules at all — an empty
  // `conflicts` list otherwise reads identically whether nothing is set up
  // or the window is genuinely clear.
  configured: boolean
}

export interface TimeSlot {
  start: string
  end: string
}

export interface FreeSlotsResponse {
  date: string
  duration_minutes: number
  slots: TimeSlot[]
  total: number
  // False when the practice has no availability rules at all. An empty
  // `slots` list otherwise reads identically whether nothing is set up or
  // the day is simply full — check this first.
  configured: boolean
}
