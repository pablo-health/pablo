// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The question above a control, for every item a practice wrote itself.
 *
 * One frame rather than a heading per renderer, because the wording arrives
 * the same way for all of them — `label` is the question, `help_text` the
 * line under it — and a patient moving through a form should meet the same
 * shape on every screen.
 *
 * **A question with no wording is not asked.** The label is what the item is,
 * so an item stored without one has nothing to put on the screen, and a
 * renderer that invented a heading would be asking a question the practice
 * never wrote. That reads as a step still to come, which is also what the
 * server does with it: publishing refuses a form with one on it.
 */

import { Unavailable } from "./DisplayItem"
import type { ItemRendererProps } from "./types"

/** The question and its help text, or null when the item carries neither. */
export function wordingOf(item: ItemRendererProps["item"]): {
  label: string
  helpText: string | null
} | null {
  const label = item.label?.trim()
  if (!label) return null
  const helpText = item.help_text?.trim()
  return { label, helpText: helpText ? helpText : null }
}

export function QuestionFrame({
  item,
  children,
}: {
  item: ItemRendererProps["item"]
  children: (headingId: string) => React.ReactNode
}) {
  const wording = wordingOf(item)
  if (wording === null) return <Unavailable />

  const headingId = `forms-question-${item.id}`
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="text-lg font-semibold text-neutral-900">
        {wording.label}
      </h2>
      {wording.helpText && (
        <p data-testid="forms-question-help" className="mt-2 text-sm text-neutral-600">
          {wording.helpText}
        </p>
      )}
      <div className="mt-4">{children(headingId)}</div>
    </section>
  )
}

/** What the review screen calls this question: the same words it was asked in. */
export function labelOf(item: ItemRendererProps["item"]): string {
  return wordingOf(item)?.label ?? ""
}
