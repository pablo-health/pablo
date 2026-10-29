// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Chart updates a note proposes (backend `app/chart_proposals/schemas.py`).
 * Kept beside the note, never in its content.
 */

import type { NoteDiagnosisCode } from "./notes"

export type ChartProposalDecision = "pending" | "accepted" | "edited" | "discarded"

export interface ProposalEvidence {
  /** The line or paragraph cited; for an intake form, the question's position on it. */
  segment_id: number
  /** The cited transcript line (or imported document's paragraph, or intake answer) as written there. */
  text: string
  /** For an intake form's proposal, the question the cited answer was given to. */
  item_id?: string | null
  /** That question's wording. */
  question?: string | null
}

/** A change to the medication list, as the visit stated it. */
export interface MedicationChange {
  /** start, stop and change are the clinician's; add is a medication the client takes. */
  action: "start" | "stop" | "change" | "add"
  drug_name: string
  /** For a change, the new value; null leaves it as it is. */
  dose: string | null
  frequency: string | null
  category: string | null
  /** Why a medication was stopped. */
  reason: string | null
  /** The medication-name words copied from the cited source. */
  heard_as: string
  /** True when those words do not unambiguously support drug_name. */
  name_uncertain: boolean
}

/** When something the clinician said they would do is due, as the session said it. */
export interface Commitment {
  /** The day the words name; null when no single day was said. */
  due_date: string | null
  /** The words that say when ("by Friday"); "" when no time was said. */
  due_said: string
}

/** The field key of a task the clinician said they would do. */
export const SESSION_TASK = "session_task"

export function isSessionTask(proposal: ChartProposal): boolean {
  return proposal.field_key === SESSION_TASK
}

export interface ChartProposal {
  id: string
  field_key: string
  /** For a list field, the entry it is about (an allergy's substance); otherwise "". */
  item_key: string
  label: string
  /** False for a medication change, which is accepted or discarded, not rewritten. */
  editable: boolean
  /** The structured change, for a medication; null for free text. */
  change: MedicationChange | null
  /**
   * For a task the clinician said they would do: when it is due. Accepting
   * files it to their tasks, tied to the client. Absent from an older server.
   */
  commitment?: Commitment | null
  /** What the chart says now; null when nothing is recorded. */
  current_text: string | null
  proposed_text: string
  what_changed: string
  evidence: ProposalEvidence[]
  /**
   * "transcript": drafted from the visit; "document": drafted from an imported
   * note's document; "note": the note's own text (an intake note); "intake": an
   * accepted intake form's answers.
   */
  origin: string
  decision: ChartProposalDecision
  decided_text: string | null
  decided_by: string | null
  decided_at: string | null
  created_at: string
}

/** How the note's proposal call last ended. */
export interface ProposalRun {
  /** "failed": the note was not checked; "skipped": its type is not checked. */
  status: "ok" | "failed" | "skipped"
  computed_at: string
  /** Whether it can be run again (a note drafted from a session's transcript). */
  retryable: boolean
}

export interface ChartProposalsResponse {
  data: ChartProposal[]
  /** Null for a note the call never ran on. */
  run: ProposalRun | null
}

export interface DecideChartProposalRequest {
  decision: "accept" | "edit" | "discard"
  /** The clinician's text, for an edit. */
  text?: string
  /** For a task: when it is due instead, null for undated. Left out, the proposed day stands. */
  due_date?: string | null
}

/** One note's pending proposals, with enough about the note to head them. */
export interface NoteChartProposalGroup {
  note_id: string
  note_type: string
  session_id: string | null
  /** The visit the note records, else when it was written. */
  date: string
  /** Where an archive import brought the note from; null otherwise. */
  source_system: string | null
  /** A reference copy of a note signed elsewhere. */
  imported: boolean
  /** The diagnosis codes an imported note carried, offered to the problem list. */
  source_diagnoses: NoteDiagnosisCode[]
  proposals: ChartProposal[]
}

/** One accepted intake form's pending proposals, with enough about the form to head them. */
export interface IntakeChartProposalGroup {
  assignment_id: string
  packet_name: string
  /** When the form was handed in, else when it was accepted. */
  date: string
  proposals: ChartProposal[]
}

/** A client's pending proposals across their notes and intake forms, newest first. */
export interface PatientChartProposalsResponse {
  groups: NoteChartProposalGroup[]
  /** Absent from a server that predates intake proposals. */
  intakes?: IntakeChartProposalGroup[]
  pending: number
}
