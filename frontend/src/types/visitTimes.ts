// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** Mirrors backend/app/models/visit_times.py. */

export type TurnLabel = "therapy" | "medication_management" | "screening_risk" | "admin"
export type RunLabel = TurnLabel | "unattributed"

export interface RecordingTurn {
  seconds: number
  /** Where the turn's span ends: the next turn's start, or where the client left. */
  end_seconds: number
  speaker: string
  text: string
  /** The confirmed label, else the proposed one; null is unattributed. */
  label: TurnLabel | null
}

export interface TurnRun {
  label: RunLabel
  start_seconds: number
  end_seconds: number
}

export interface DictatedTime {
  start: string | null
  end: string | null
  minutes: number | null
  as_dictated: string
}

export interface PsychotherapyWindow {
  offered: boolean
  end_seconds: number | null
  turns: RecordingTurn[]
  runs: TurnRun[]
  labeled_minutes: number | null
  cue_seconds: number | null
  dictated: DictatedTime | null
  confirmed_start_seconds: number | null
  confirmed_minutes: number | null
  contiguous: boolean | null
  /** The clinician confirmed the turn labels (rather than a start or minutes). */
  labels_confirmed: boolean
  window_text: string | null
  dictated_time: string | null
  disagrees: boolean
}

export interface VisitTimes {
  started_at: string | null
  ended_at: string | null
  total_minutes: number | null
  /** When the recording began; every offset in seconds counts from here. */
  recording_started_at: string | null
  client_present_end_seconds: number | null
  clinician_addendum_seconds: number | null
  psychotherapy: PsychotherapyWindow | null
  total_with_documentation_minutes: number | null
}

export interface ConfirmPsychotherapyWindowRequest {
  labels?: { seconds: number; label: TurnLabel }[]
  start_seconds?: number
  minutes?: number
  time_zone: string
  resolution?: "use_confirmed" | "keep_dictated"
}
