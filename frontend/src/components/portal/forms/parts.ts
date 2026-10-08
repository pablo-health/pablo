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
 * **Instructions lead into the question after them.** A block of
 * instructions collects nothing either, so it is shown above the next
 * question in its part rather than as an uncounted screen of its own. Only
 * instructions with nothing after them in their part keep a screen.
 *
 * Built from the questions this patient is shown, so a part whose questions
 * are all hidden by a rule disappears rather than showing as an empty count.
 */

import type { IntakeAssignmentItem } from "@/lib/api/patientIntake"
import { ruleOf } from "@/lib/intake/visibility"
import { OPENING_PART_TITLE, PART_VERBS } from "./formsCopy"
import { rendererFor } from "./renderers/registry"

export interface FormPart {
  /** The section's item id, or `opening` for what comes before the first one. */
  key: string
  /** The section's title; null when there is nothing honest to call it. */
  title: string | null
  /** The screens of this part, in order. Never a section. */
  screens: IntakeAssignmentItem[]
  /**
   * Instructions shown above a screen rather than on one of their own,
   * keyed by the id of the screen they lead into.
   */
  notes: Record<string, IntakeAssignmentItem[]>
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
  let current: FormPart = { key: "opening", title: null, screens: [], notes: {} }
  // Instructions waiting for the next screen in this part to ride on.
  let pending: IntakeAssignmentItem[] = []
  const close = () => {
    // Nothing after them in this part: they keep a screen of their own
    // rather than leaking into a part with a different name.
    current.screens.push(...pending)
    pending = []
    parts.push(current)
  }
  for (const item of items) {
    if (item.item_type === "section") {
      close()
      const title = typeof item.config.title === "string" ? item.config.title.trim() : ""
      current = { key: item.id, title: title === "" ? null : title, screens: [], notes: {} }
      continue
    }
    if (item.item_type === "instructions") {
      pending.push(item)
      continue
    }
    if (pending.length > 0) {
      current.notes[item.id] = pending
      pending = []
    }
    current.screens.push(item)
  }
  close()

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

/** The instructions shown above one screen, in the order the form gives them. */
export function notesFor(parts: FormPart[], item: IntakeAssignmentItem): IntakeAssignmentItem[] {
  const part = parts.find((candidate) => candidate.screens.includes(item))
  return part?.notes[item.id] ?? []
}

/** What a part asks the patient to do, by the one kind of item it holds. */
const VERB_BY_TYPE: Record<string, string> = {
  consent_document: PART_VERBS.sign,
  insurance_card: PART_VERBS.photo,
  document_request: PART_VERBS.file,
}

/** Item types that are questions to answer, however they are asked. */
const ANSWER_TYPES = new Set([
  "free_text",
  "single_choice",
  "multi_choice",
  "yes_no",
  "scale",
  "number",
  "date",
  "instrument",
  "emergency_contact",
  "guardian",
])

/**
 * What a part asks the patient to do: read and sign, answer, send a photo
 * or send a file. Only when every item in it collecting something is the
 * same kind; a part that mixes them gets no verb rather than a wrong one.
 */
export function verbOf(part: FormPart): string | null {
  const kinds = new Set(
    part.screens
      .filter(collectsAnswer)
      .map((item) =>
        item.item_type in VERB_BY_TYPE
          ? VERB_BY_TYPE[item.item_type]
          : ANSWER_TYPES.has(item.item_type)
            ? PART_VERBS.answer
            : null,
      ),
  )
  if (kinds.size !== 1) return null
  const [only] = kinds
  return only
}

/** Where one screen sits: which part, and which question within it. */
export interface PartPlace {
  title: string | null
  /** What the part asks them to do ("Read and sign"), or null when it mixes kinds. */
  verb: string | null
  /** 1-based, among the parts of this walk. */
  part: number
  parts: number
  /**
   * 1-based among the part's questions that collect something; null
   * otherwise. A follow-up shares the number of the question it follows.
   */
  question: { index: number; total: number } | null
}

/**
 * Where one screen sits in its part.
 *
 * **A follow-up is counted as part of the question that opened it.** A
 * question a rule shows only after an answer earlier in the same part would
 * otherwise grow the total under the patient's feet — "1 of 7" becoming "2
 * of 8" one press later. So the total counts only the questions the part
 * asks whatever the answers, and a follow-up carries the number of the
 * question it follows from. A rule pointing at an earlier part does not
 * move this part's count, so a question it shows is counted normally.
 */
export function placeOf(parts: FormPart[], item: IntakeAssignmentItem): PartPlace | null {
  const partIndex = parts.findIndex((part) => part.screens.includes(item))
  if (partIndex < 0) return null
  const part = parts[partIndex]
  const counted = part.screens.filter(
    (screen) => collectsAnswer(screen) && triggerIn(part, screen) === null,
  )
  const index = collectsAnswer(item) ? counted.indexOf(rootOf(part, item)) : -1
  return {
    title: part.title,
    verb: verbOf(part),
    part: partIndex + 1,
    parts: parts.length,
    question: index < 0 ? null : { index: index + 1, total: counted.length },
  }
}

/** The question in this part whose answer decides whether `item` is shown. */
function triggerIn(part: FormPart, item: IntakeAssignmentItem): IntakeAssignmentItem | null {
  const rule = ruleOf(item.config)
  if (rule === null) return null
  return part.screens.find((screen) => screen.key === rule.item_key && screen !== item) ?? null
}

/** The question a follow-up — or a follow-up of a follow-up — hangs from. */
function rootOf(part: FormPart, item: IntakeAssignmentItem): IntakeAssignmentItem {
  // Rules only look backwards, so this ends; the set is there so a stored
  // form that broke that rule cannot hang the walk.
  const seen = new Set<IntakeAssignmentItem>([item])
  let current = item
  for (;;) {
    const trigger = triggerIn(part, current)
    if (trigger === null || seen.has(trigger)) return current
    seen.add(trigger)
    current = trigger
  }
}
