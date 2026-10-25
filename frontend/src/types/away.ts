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

export interface AwayPeriod {
  id: string
  start_date: string
  end_date: string
  return_date: string
  covering_user_id: string | null
  covering_name: string | null
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
  covering_options: CoveringOption[]
}
