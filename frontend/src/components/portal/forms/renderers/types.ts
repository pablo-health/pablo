// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What a renderer is, and what the walk gives it.
 *
 * One renderer per item type, looked up in the registry. A new item type is
 * a renderer added there and nothing else: the walk, the review screen and
 * the save path have no list of types between them.
 */

import type { ComponentType } from "react"
import type { IntakeAssignmentItem, IntakeForm } from "@/lib/api/patientIntake"

/** An answer in flight, in the shape the save route stores. */
export type AnswerValue = Record<string, unknown>

export interface ItemRendererProps {
  item: IntakeAssignmentItem
  /** What is saved or typed so far; null until the patient touches it. */
  value: AnswerValue | null
  onChange: (value: AnswerValue) => void
  /**
   * The wording the engine owns — the patient's own identity fields, the
   * reason prompt, each measure's items and anchors. Null while it is still
   * loading, or on a deployment whose route did not answer.
   */
  form: IntakeForm | null
}

export interface ItemRenderer {
  Component: ComponentType<ItemRendererProps>
  /**
   * True when this type collects an answer the save route will store.
   *
   * False for the headings and paragraphs that collect nothing, and for the
   * types whose question text the API does not carry yet — both are walked
   * past rather than saved, which is what the server does with them too.
   */
  answerable: boolean
  /**
   * What to call this question on the review screen.
   *
   * The same wording the question itself carries, so a row and the screen
   * it edits read as the same question. Only ever asked of an answerable
   * renderer, which is why the display ones return an empty string.
   */
  label: (item: IntakeAssignmentItem, form: IntakeForm | null) => string
  /**
   * One line for the review screen, or null when there is nothing to show.
   *
   * Never a score and never a band: the review screen repeats what the
   * patient said, not what it means.
   */
  summary: (value: AnswerValue | null, item: IntakeAssignmentItem, form: IntakeForm | null) => string | null
  /** True when this item carries the crisis line. */
  crisisFooter?: boolean
}
