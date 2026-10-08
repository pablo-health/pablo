// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Chart updates a note proposes (backend `app/chart_proposals/schemas.py`).
 * Kept beside the note, never in its content.
 */

export type ChartProposalDecision = "pending" | "accepted" | "edited" | "discarded"

export interface ProposalEvidence {
  segment_id: number
  /** The cited transcript line as the transcript has it. */
  text: string
}

export interface ChartProposal {
  id: string
  field_key: string
  /** For a list field, the entry it is about (an allergy's substance); otherwise "". */
  item_key: string
  label: string
  /** What the chart says now; null when nothing is recorded. */
  current_text: string | null
  proposed_text: string
  what_changed: string
  evidence: ProposalEvidence[]
  /** "transcript": drafted from the visit; "note": the note's own text (an intake). */
  origin: string
  decision: ChartProposalDecision
  decided_text: string | null
  decided_by: string | null
  decided_at: string | null
  created_at: string
}

export interface ChartProposalsResponse {
  data: ChartProposal[]
}

export interface DecideChartProposalRequest {
  decision: "accept" | "edit" | "discard"
  /** The clinician's text, for an edit. */
  text?: string
}
