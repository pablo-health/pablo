// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** Mirrors backend/app/models/visit_times.py. */

export interface RecordingTurn {
  seconds: number
  speaker: string
  text: string
}

export type StartSource = "spoken_cue" | "marked" | "attributed"

export interface StartCandidate {
  seconds: number
  source: StartSource
}

export interface PsychotherapyWindow {
  offered: boolean
  end_seconds: number | null
  turns: RecordingTurn[]
  candidates: StartCandidate[]
  stated_clock_time: string | null
  confirmed_start_seconds: number | null
  confirmed_minutes: number | null
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
  start_seconds?: number
  minutes?: number
  time_zone: string
  resolution?: "use_confirmed" | "keep_dictated"
}
