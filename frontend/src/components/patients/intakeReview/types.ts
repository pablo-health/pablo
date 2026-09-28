// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What a read-only view of one question is, and what the review gives it.
 *
 * The chart's half of the portal's renderers: one view per item type, drawn
 * from the same answer shapes and the same wording, with every control
 * disabled. The portal's renderers stay the patient's — they sign, upload
 * and save through a patient session — and these never write anything.
 *
 * Everything a view needs arrives from the one audited review read, plus
 * the files the chart has already listed and, for a consent item, the
 * practice's own document text. None of it is computed here: a view shows
 * what was recorded, and what "finished" means is still the server's.
 */

import type { ComponentType } from "react"

import type {
  IntakeChartArtifact,
  IntakeReviewItem,
  IntakeReviewSignature,
} from "@/lib/api/intakeReview"
import type { IntakeForm } from "@/lib/api/patientIntake"

export interface ItemViewProps {
  item: IntakeReviewItem
  /** The wording the engine owns, served with the review. */
  form: IntakeForm
  /** Signatures taken against THIS question. Empty for every other type. */
  signatures: IntakeReviewSignature[]
  /** Files that arrived for THIS question. Empty for every other type. */
  artifacts: IntakeChartArtifact[]
}

export interface ItemView {
  Component: ComponentType<ItemViewProps>
  /**
   * False for a heading or a paragraph: nothing to answer, so nothing to
   * send back, write down or call unanswered.
   */
  answerable: boolean
}
