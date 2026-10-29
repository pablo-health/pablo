// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** Dates are ISO calendar days (`YYYY-MM-DD`) on the clinician's own calendar. */
export interface AwayDates {
  /** First day away. */
  start_date: string
  /** Last day away, inclusive. */
  end_date: string
  /** The day the clinician is back. Defaults to the day after `end_date`. */
  return_date?: string | null
  covering_user_id?: string | null
}

export interface CreateAwayPeriodRequest extends AwayDates {
  acknowledgment_enabled: boolean
  acknowledgment_text: string
}

/** Only the fields sent change. New dates must carry `acknowledgment_text`. */
export type UpdateAwayPeriodRequest = Partial<CreateAwayPeriodRequest>

export interface AcknowledgmentPreview {
  /** The suggested reply, for the clinician to review and edit. */
  acknowledgment_text: string
  /** Added after the reply every time. Not editable. */
  safety_lines: string
  return_date: string
}

/** Where a request to cover stands. Access starts only once `accepted`. */
export type CoverageStatus = "requested" | "accepted" | "declined"

export interface AwayPeriod {
  id: string
  start_date: string
  end_date: string
  return_date: string
  covering_user_id: string | null
  covering_name: string | null
  /** `null` when nobody is asked to cover. */
  coverage_status: CoverageStatus | null
  acknowledgment_enabled: boolean
  acknowledgment_text: string
  safety_lines: string
  /** Away today. */
  active: boolean
  ended_at: string | null
}

export interface CoveringOption {
  user_id: string
  name: string
}

export interface AwayPeriodList {
  periods: AwayPeriod[]
  /** Covered periods that are over, ending in the last 30 days, latest first. */
  recently_covered: AwayPeriod[]
  covering_options: CoveringOption[]
}

/** One thing the covering colleague did on a client's record. */
export interface CoverageActivityItem {
  at: string
  /** An audit action, e.g. `patient_viewed`. */
  action: string
  resource_type: string
  resource_id: string
  patient_id: string
  client_name: string | null
}

export interface CoverageActivity {
  covering_name: string | null
  /** Newest first. */
  items: CoverageActivityItem[]
}

/** An away period, as seen by the colleague asked to cover it. */
export interface CoverageRequest {
  period_id: string
  clinician_id: string
  clinician_name: string | null
  start_date: string
  end_date: string
  return_date: string
  status: CoverageStatus
  /** The colleague is away today. */
  active: boolean
}

export interface CoverageRequestList {
  requests: CoverageRequest[]
}
