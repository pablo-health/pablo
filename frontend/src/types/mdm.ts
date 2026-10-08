// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** Mirrors backend/app/models/mdm_review.py. */

export type MdmElement = "problems" | "data" | "risk"

export interface MdmElementReview {
  element: MdmElement
  chosen: string | null
  /** The draft's one sentence of evidence for this element. */
  evidence: string
  /** The level the computed MDM level needed from this element; null until all three are chosen. */
  required: string | null
  meets: boolean | null
}

export interface MdmReview {
  /** Problems and risk first: at a medication visit they usually decide the level. */
  elements: MdmElementReview[]
  new_patient: boolean
  level: string | null
  em_code: string | null
  has_psychotherapy: boolean
  psychotherapy_minutes: number | null
  add_on: string | null
  /** False while a psychotherapy portion has no confirmed minutes. */
  add_on_known: boolean
  billing_methods: Array<"mdm" | "time">
  dictated_em_code: string | null
  dictated_add_on: string | null
  em_disagrees: boolean
  add_on_disagrees: boolean
  /** The note's visit details with the computed codes in place; null when that changes nothing. */
  visit_details_with_codes: string | null
}

export interface MdmChoicesRequest {
  problems: string | null
  data: string | null
  risk: string | null
  new_patient: "new" | "established" | null
}
