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
  /** The appointment type this rule governs, or null for every type. */
  appointment_type_id?: string | null
  /** False when a working-hours window is claimed for its type alone. */
  allow_other_types?: boolean
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
  /** Omit or null for a rule that applies to every appointment type. */
  appointment_type_id?: string | null
  allow_other_types?: boolean
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
  /** The appointment type the sentence scoped this rule to, or null for all. */
  appointment_type_id?: string | null
  allow_other_types?: boolean
}

export interface ParseAvailabilityRulesResponse {
  proposals: ProposedAvailabilityRule[]
  could_not_parse: string | null
  refusal_reason?: string | null
  exclusive: boolean
  existing_conflicting_rules: AvailabilityRule[]
  /** On an unknown-appointment-type refusal, the kind the sentence named. */
  unknown_appointment_type?: string | null
  /** On an ambiguous refusal between two meanings, both meanings to pick from. */
  readings?: ParseReading[]
}

/** One meaning of an ambiguous sentence, with the rules it would store. */
export interface ParseReading {
  label: string
  proposals: ProposedAvailabilityRule[]
}

/** A rule that applies to every appointment type, which is what the general
 * settings cards (working-hours grid, limits, blocked time) edit. A rule
 * scoped to one type is listed separately, so it is never shown, edited or
 * deleted as if it were the practice's general setting. */
export function isPracticeWide(rule: Pick<AvailabilityRule, "appointment_type_id">): boolean {
  return !rule.appointment_type_id
}

/** What to send when confirming a proposal: its type scope travels with it,
 * so "two intakes a week" is never saved as "two appointments a week". */
export function proposalToCreateRequest(
  proposal: Pick<
    ProposedAvailabilityRule,
    "rule_type" | "enforcement" | "params" | "appointment_type_id" | "allow_other_types"
  >,
): CreateAvailabilityRuleRequest {
  return {
    rule_type: proposal.rule_type,
    enforcement: proposal.enforcement,
    params: proposal.params,
    appointment_type_id: proposal.appointment_type_id ?? null,
    allow_other_types: proposal.allow_other_types ?? true,
  }
}

/**
 * A single rule a proposed booking window runs into. Mirrors
 * ConflictResponse in backend/app/models/scheduling.py — rule type and
 * enforcement level plus the engine's message, with no rule params.
 */
export interface ConflictResponse {
  rule_type: ConflictKind
  enforcement: EnforcementLevel
  message: string
}

/** A time that overlaps busy time on the therapist's calendar. No rule
 * describes it, it is always soft, and it never refuses a booking. */
export const CALENDAR_BUSY = "calendar_busy"

/** What a conflict is about: one of the therapist's rules, or busy time. */
export type ConflictKind = RuleType | typeof CALENDAR_BUSY

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
