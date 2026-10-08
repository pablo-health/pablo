// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The confirm step's timeline: client-present turns, each labeled, as runs.
 *
 * Mirrors app.notes.visit_times: a turn spans from its start to the next
 * turn's start (the last to where the client left), therapy minutes are the
 * sum of the turns labeled therapy, rounded down, and an unlabeled turn is
 * not counted. Labels are held as an array parallel to the turns.
 */

import type { RecordingTurn, RunLabel, TurnLabel } from "@/types/visitTimes"

const SECONDS_PER_MINUTE = 60

export type Labels = (TurnLabel | null)[]

export interface Run {
  label: RunLabel
  /** Index of the run's first and last turn. */
  first: number
  last: number
  start: number
  end: number
}

export const TURN_LABELS: TurnLabel[] = ["therapy", "medication_management", "screening_risk", "admin"]

export const LABEL_NAME: Record<RunLabel, string> = {
  therapy: "Therapy",
  medication_management: "Medication",
  screening_risk: "Screening and risk",
  admin: "Other",
  unattributed: "Not labeled",
}

export function runsOf(turns: RecordingTurn[], labels: Labels): Run[] {
  const runs: Run[] = []
  turns.forEach((turn, i) => {
    const label: RunLabel = labels[i] ?? "unattributed"
    const previous = runs[runs.length - 1]
    if (previous && previous.label === label) {
      previous.last = i
      previous.end = turn.end_seconds
    } else {
      runs.push({ label, first: i, last: i, start: turn.seconds, end: turn.end_seconds })
    }
  })
  return runs
}

export function therapyMinutes(turns: RecordingTurn[], labels: Labels): number {
  const seconds = turns.reduce(
    (sum, turn, i) => (labels[i] === "therapy" ? sum + turn.end_seconds - turn.seconds : sum),
    0,
  )
  return Math.floor(seconds / SECONDS_PER_MINUTE)
}

/** Every turn of ``run`` takes ``label``. */
export function relabel(labels: Labels, run: Run, label: TurnLabel): Labels {
  return labels.map((current, i) => (i >= run.first && i <= run.last ? label : current))
}

/**
 * Move the boundary that starts at turn ``from`` to turn ``to``: the turns
 * between change sides. Moving it later grows the run before it; earlier
 * grows the run after it.
 */
export function moveBoundary(labels: Labels, from: number, to: number): Labels {
  if (to === from) return labels
  const grown = to > from ? labels[from - 1] : labels[from]
  const [low, high] = to > from ? [from, to] : [to, from]
  return labels.map((current, i) => (i >= low && i < high ? (grown ?? null) : current))
}

/** The turn nearest ``seconds``, kept within ``[min, max]``. */
export function nearestTurn(turns: RecordingTurn[], seconds: number, min: number, max: number): number {
  let best = min
  for (let i = min; i <= max; i++) {
    if (Math.abs(turns[i].seconds - seconds) < Math.abs(turns[best].seconds - seconds)) best = i
  }
  return best
}

/**
 * Fewer minutes than this left for the medical visit is flagged.
 *
 * Whether the time beside the therapy fits the visit's level is the
 * clinician's judgment, so no level enters here. This is one floor below
 * which almost no medical visit happened at all: a prompt to look again,
 * never a rule.
 */
export const THIN_REMAINDER_MINUTES = 5

/** Minutes with the client that were not therapy: the medical visit's share. */
export function remainderMinutes(presentSeconds: number, therapy: number): number {
  return Math.max(Math.floor(presentSeconds / SECONDS_PER_MINUTE) - therapy, 0)
}

export function isThin(remainder: number): boolean {
  return remainder < THIN_REMAINDER_MINUTES
}
