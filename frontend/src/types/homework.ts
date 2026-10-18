// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Homework on the chart: what a client was asked to do between visits.
 *
 * Suggestions read from a note are chart updates (`types/chartProposals`, field
 * key "homework"), decided at signing; accepting one makes an assignment here.
 */

export type HomeworkStatus = "assigned" | "done" | "withdrawn"

export type HomeworkOrigin = "note" | "clinician"

export interface HomeworkAssignment {
  id: string
  /** The note an accepted suggestion came from; null for one added by hand. */
  note_id: string | null
  source_field: string | null
  /** The wording that was suggested, when the clinician changed it. */
  proposed_text: string | null
  text: string
  status: HomeworkStatus
  origin: HomeworkOrigin
  assigned_at: string
  completed_at: string | null
  client_note: string | null
  created_at: string
}

export interface HomeworkOverview {
  /** Assigned, done and withdrawn, newest first. */
  assignments: HomeworkAssignment[]
}

/** Server-side limit, mirrored so the input stops where the route would reject. */
export const HOMEWORK_TEXT_MAX = 300
