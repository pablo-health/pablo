// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Availability rule API types
 *
 * Mirrors backend `app.scheduling_engine.models.availability.RuleType` and
 * the request/response models in `app.models.scheduling`. `params` varies by
 * rule type — see the per-type interfaces below, which match the exact keys
 * the evaluation engine reads (`app.scheduling_engine.services.availability`).
 */

export type RuleType =
  | "working_hours"
  | "block_day_of_week"
  | "block_time_range"
  | "max_per_day"
  | "buffer_before"
  | "buffer_after"
  | "block_date_range"
  | "block_specific_dates"

export type EnforcementLevel = "hard" | "soft"

export interface WorkingHoursParams {
  day_of_week: number
  start: string
  end: string
}

export interface BlockDayOfWeekParams {
  day_of_week: number
}

export interface BlockTimeRangeParams {
  start: string
  end: string
}

export interface MaxPerDayParams {
  max: number
}

export interface BufferParams {
  minutes: number
}

export interface BlockDateRangeParams {
  start_date: string
  end_date: string
}

export interface BlockSpecificDatesParams {
  dates: string[]
}

export type AvailabilityRuleParams =
  | WorkingHoursParams
  | BlockDayOfWeekParams
  | BlockTimeRangeParams
  | MaxPerDayParams
  | BufferParams
  | BlockDateRangeParams
  | BlockSpecificDatesParams

export interface AvailabilityRuleResponse {
  id: string
  user_id: string
  rule_type: string
  enforcement: string
  params: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
}

export interface AvailabilityRuleListResponse {
  data: AvailabilityRuleResponse[]
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

export const DAY_OF_WEEK_OPTIONS = [
  { value: 0, label: "Monday" },
  { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" },
  { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
] as const

export const RULE_TYPE_OPTIONS: {
  value: RuleType
  label: string
  description: string
}[] = [
  {
    value: "working_hours",
    label: "Working hours",
    description: "Only allow bookings on one weekday within a time window.",
  },
  {
    value: "block_day_of_week",
    label: "Block a day of the week",
    description: "Block an entire weekday, every week.",
  },
  {
    value: "block_time_range",
    label: "Block a time range",
    description: "Block a recurring time window every day.",
  },
  {
    value: "max_per_day",
    label: "Max appointments per day",
    description: "Cap how many appointments can be booked in a single day.",
  },
  {
    value: "buffer_before",
    label: "Buffer before appointments",
    description: "Require a gap before every appointment.",
  },
  {
    value: "buffer_after",
    label: "Buffer after appointments",
    description: "Require a gap after every appointment.",
  },
  {
    value: "block_date_range",
    label: "Block a date range",
    description: "Block a span of calendar dates, e.g. a vacation.",
  },
  {
    value: "block_specific_dates",
    label: "Block specific dates",
    description: "Block one or more individual dates, e.g. holidays.",
  },
]
