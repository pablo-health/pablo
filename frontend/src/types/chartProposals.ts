// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Chart updates a note proposes (backend `app/chart_proposals/schemas.py`).
 * Kept beside the note, never in its content.
 */

export type ChartProposalDecision = "pending" | "accepted" | "edited" | "discarded"

export interface ProposalEvidence {
  segment_id: number
  /** The cited transcript line (or imported document's paragraph) as written there. */
  text: string
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
  /** What the chart says now; null when nothing is recorded. */
  current_text: string | null
  proposed_text: string
  what_changed: string
  evidence: ProposalEvidence[]
  /**
   * "transcript": drafted from the visit; "document": drafted from an imported
   * note's document; "note": the note's own text (an intake).
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
}
