// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A form's sections, read as the parts a patient works through.
 *
 * A `section` item is the boundary a form already carries: everything after
 * one, up to the next, belongs to it. So a long form reads as the handful of
 * things the practice put on it — a consent, a history, a measure — each
 * with a short count of its own, rather than as one count in the dozens.
 *
 * **A section is a heading, not a screen.** It collects nothing, so giving it
 * a screen of its own is a Continue press that does nothing. Its title rides
 * above each question in its part instead, and the walk never lands on it.
 *
 * **What comes before the first section is a part too.** A form opens with
 * whatever the practice put first. It is called "About you" only when that
 * is what it is — the identity check and the reason for coming in — and
 * only when the form has other parts to tell it apart from. Anything else
 * gets no heading rather than a name nobody wrote.
 *
 * Built from the questions this patient is shown, so a part whose questions
 * are all hidden by a rule disappears rather than showing as an empty count.
 */

import type { IntakeAssignmentItem } from "@/lib/api/patientIntake"
import { OPENING_PART_TITLE } from "./formsCopy"
import { rendererFor } from "./renderers/registry"

export interface FormPart {
  /** The section's item id, or `opening` for what comes before the first one. */
  key: string
  /** The section's title; null when there is nothing honest to call it. */
  title: string | null
  /** The screens of this part, in order. Never a section. */
  screens: IntakeAssignmentItem[]
}

/** The item types whose only job is to say who the patient is and why they came. */
const ABOUT_YOU_TYPES = new Set(["demographics", "reason"])

/** Whether an item collects something, however it is written. */
export function collectsAnswer(item: IntakeAssignmentItem): boolean {
  const renderer = rendererFor(item.item_type)
  return renderer.answerable || renderer.writesItself === true
}

/**
 * Split items, already in the order the form asks them, into parts.
 *
 * Parts with no screens are dropped: a section with nothing under it, or
 * one whose questions a rule has hidden, has nothing to walk.
 */
export function partsOf(items: IntakeAssignmentItem[]): FormPart[] {
  const parts: FormPart[] = []
  let current: FormPart = { key: "opening", title: null, screens: [] }
  for (const item of items) {
    if (item.item_type === "section") {
      parts.push(current)
      const title = typeof item.config.title === "string" ? item.config.title.trim() : ""
      current = { key: item.id, title: title === "" ? null : title, screens: [] }
      continue
    }
    current.screens.push(item)
  }
  parts.push(current)

  const walked = parts.filter((part) => part.screens.length > 0)
  const opening = walked[0]
  if (walked.length > 1 && opening.key === "opening" && isAboutYou(opening)) {
    walked[0] = { ...opening, title: OPENING_PART_TITLE }
  }
  return walked
}

function isAboutYou(part: FormPart): boolean {
  const collecting = part.screens.filter(collectsAnswer)
  return collecting.length > 0 && collecting.every((item) => ABOUT_YOU_TYPES.has(item.item_type))
}

/** Keep only the screens `keep` accepts, dropping parts left empty. */
export function narrowParts(
  parts: FormPart[],
  keep: (item: IntakeAssignmentItem) => boolean,
): FormPart[] {
  return parts
    .map((part) => ({ ...part, screens: part.screens.filter(keep) }))
    .filter((part) => part.screens.length > 0)
}

/** Where one screen sits: which part, and which question within it. */
export interface PartPlace {
  title: string | null
  /** 1-based, among the parts of this walk. */
  part: number
  parts: number
  /** 1-based among the part's questions that collect something; null otherwise. */
  question: { index: number; total: number } | null
}

export function placeOf(parts: FormPart[], item: IntakeAssignmentItem): PartPlace | null {
  const partIndex = parts.findIndex((part) => part.screens.includes(item))
  if (partIndex < 0) return null
  const part = parts[partIndex]
  const counted = part.screens.filter(collectsAnswer)
  const index = counted.indexOf(item)
  return {
    title: part.title,
    part: partIndex + 1,
    parts: parts.length,
    question: index < 0 ? null : { index: index + 1, total: counted.length },
  }
}
