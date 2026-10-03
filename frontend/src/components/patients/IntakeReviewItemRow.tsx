// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"

import { RENDERED_ITEM_TYPES, rendererFor } from "@/components/portal/forms/renderers/registry"
import type { ReadOnlySource } from "@/components/portal/forms/renderers/types"
import type { IntakeReviewItem } from "@/lib/api/intakeReview"
import type { IntakeArtifact, IntakeForm } from "@/lib/api/patientIntake"
import type { PeopleWords } from "@/lib/peopleTerm"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"

const COPY = {
  notAsked: "Not asked.",
  noAnswer: "No answer",
  provenance: (people: PeopleWords) => ({ patient: people.One, clinician: "Entered by practice" }),
  earlier: (n: number) => (n === 1 ? "1 earlier answer" : `${n} earlier answers`),
  earlierDetail: (n: number) => (n === 1 ? "One earlier answer was replaced." : `${n} earlier answers were replaced.`),
  enter: (people: PeopleWords) => `Enter for ${people.one}`,
  entryLabel: "Answer",
  entrySave: "Save",
  entryCancel: "Cancel",
}

const LINK = "text-xs font-medium text-primary-600 hover:text-primary-700"
const CHIP = "inline-block rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600"

/**
 * The answer to a question the portal has no renderer for, as a line of
 * text: a `text` field when there is one, the mapping spelled out otherwise.
 * Shown rather than hidden, because the patient's answer is still theirs.
 */
function spelledOut(value: Record<string, unknown> | null): string | null {
  if (!value) return null
  if (typeof value.text === "string" && value.text !== "") return value.text
  const parts = Object.entries(value)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
  return parts.length > 0 ? parts.join(" · ") : null
}

export interface IntakeReviewItemRowProps {
  item: IntakeReviewItem
  form: IntakeForm
  readOnly: ReadOnlySource
  artifacts: IntakeArtifact[]
  /** False when a visibility rule kept this question off the patient's screen. */
  shown: boolean
  selectable: boolean
  selected: boolean
  onSelect: (itemId: string, checked: boolean) => void
  canEnter: boolean
  saving: boolean
  onSaveEntry: (itemId: string, text: string) => void
}

/**
 * One question, drawn by the portal's own renderer with the patient's answer
 * in it, and the practice's review facts and actions around it.
 *
 * The renderer is the one the patient answered on, given `readOnly`, so the
 * chart shows exactly what was on their screen. Everything the chart adds —
 * the checkbox to send it back, who put the answer there, how many answers
 * it replaced, writing one down — is hidden when the form is printed, which
 * is a copy of the form rather than of the review.
 *
 * A heading or a paragraph is neither answered nor sent back, so it carries
 * none of that.
 */
export function IntakeReviewItemRow(props: IntakeReviewItemRowProps) {
  const { item, form, readOnly, artifacts, shown, selectable, selected, onSelect, canEnter, saving, onSaveEntry } = props
  const [showEarlier, setShowEarlier] = useState(false)
  const [entryOpen, setEntryOpen] = useState(false)
  const [entryText, setEntryText] = useState("")
  const people = usePeopleTerm()
  const id = item.id
  const drawable = RENDERED_ITEM_TYPES.includes(item.item_type)
  const renderer = rendererFor(item.item_type)
  const isQuestion = !drawable || renderer.answerable || renderer.writesItself === true
  // Signing and sending a file are the patient's own acts; a heading has no answer.
  const enterable = !drawable || renderer.answerable
  const question = renderer.label(item, form) || item.key
  const Renderer = renderer.Component

  return (
    <li className="border-t border-border py-4 first:border-t-0" data-testid={`intake-review-item-${id}`}
      data-print-keep={item.item_type === "instrument" ? undefined : ""}>
      <div className="flex items-start gap-3">
        {selectable && isQuestion && (
          <input type="checkbox" className="mt-1.5 print:hidden" checked={selected} aria-label={question}
            onChange={(e) => onSelect(id, e.target.checked)} data-testid={`intake-review-select-${id}`} />
        )}
        <div className="min-w-0 flex-1" data-testid={`intake-review-value-${id}`}>
          {!drawable ? (
            <div>
              <p className="text-sm font-medium text-neutral-900">{item.label?.trim() || item.key}</p>
              <p className="mt-1 whitespace-pre-wrap text-sm text-neutral-900">{spelledOut(item.value) ?? COPY.noAnswer}</p>
            </div>
          ) : shown ? (
            <>
              <Renderer item={item} value={item.value} form={form} artifacts={artifacts} readOnly={readOnly} />
              {renderer.answerable && item.value === null && (
                <p className="mt-2 text-sm text-neutral-500">{COPY.noAnswer}</p>
              )}
            </>
          ) : (
            <div className="text-neutral-500">
              <p className="text-sm font-medium">{question}</p>
              <p className="mt-1 text-sm">{COPY.notAsked}</p>
            </div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-3 print:hidden">
            {item.provenance && (
              <span className={CHIP} data-testid={`intake-review-provenance-${id}`}>
                {COPY.provenance(people)[item.provenance]}
              </span>
            )}
            {item.superseded_count > 0 && (
              <button type="button" className={LINK} aria-expanded={showEarlier}
                onClick={() => setShowEarlier((open) => !open)}
                data-testid={`intake-review-earlier-toggle-${id}`}>
                {COPY.earlier(item.superseded_count)}
              </button>
            )}
            {canEnter && enterable && !entryOpen && (
              <button type="button" className={LINK} onClick={() => setEntryOpen(true)}
                data-testid={`intake-review-enter-${id}`}>
                {COPY.enter(people)}
              </button>
            )}
          </div>
          {showEarlier && (
            <p className="mt-1 text-xs text-neutral-500 print:hidden" data-testid={`intake-review-earlier-detail-${id}`}>
              {COPY.earlierDetail(item.superseded_count)}
            </p>
          )}
          {canEnter && enterable && entryOpen && (
            <div className="mt-2 flex flex-wrap items-center gap-2 print:hidden">
              <input type="text" className="input min-w-0 flex-1" value={entryText} aria-label={COPY.entryLabel}
                onChange={(e) => setEntryText(e.target.value)}
                data-testid={`intake-review-entry-input-${id}`} />
              <button type="button" className="btn-primary text-xs" disabled={saving || entryText.trim() === ""}
                onClick={() => onSaveEntry(id, entryText.trim())}
                data-testid={`intake-review-entry-save-${id}`}>
                {COPY.entrySave}
              </button>
              <button type="button" className="text-xs text-neutral-500" onClick={() => setEntryOpen(false)}
                data-testid={`intake-review-entry-cancel-${id}`}>
                {COPY.entryCancel}
              </button>
            </div>
          )}
        </div>
      </div>
    </li>
  )
}
