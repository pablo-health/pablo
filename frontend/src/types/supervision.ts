// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Supervision and delegation relationship types.
 *
 * Mirrors backend/app/routes/supervision.py response shapes.
 */

export type RelationshipType =
  | "clinical_supervision"
  | "prescriptive_authority"
  | "delegated_prescribing"
  | "administrative_supervision"

export type RelationshipStatus = "active" | "lapsed" | "pending"

export interface SupervisionRelationship {
  id: string
  relationship_type: RelationshipType
  supervisor_name: string
  supervisor_credential: string | null
  supervisor_dea: string | null
  supervisor_license: string | null
  state: string | null
  effective_date: string | null
  review_cadence_days: number | null
  next_review_date: string | null
  authority_ref: string | null
  status: RelationshipStatus
}

export interface SupervisionRelationshipPayload {
  relationship_type: RelationshipType
  supervisor_name: string
  supervisor_credential: string | null
  supervisor_dea: string | null
  supervisor_license: string | null
  state: string | null
  effective_date: string | null
  review_cadence_days: number | null
  next_review_date: string | null
  authority_ref: string | null
  status: RelationshipStatus
}

export type HoursKind = "individual" | "group" | "peer" | "didactic" | "other"

export interface SupervisionHoursEntry {
  id: string
  logged_date: string
  hours: number
  kind: HoursKind
  supervisor: string | null
  notes: string | null
}

export interface SupervisionHoursPayload {
  logged_date: string
  hours: number
  kind: HoursKind
  supervisor: string | null
  notes: string | null
}
