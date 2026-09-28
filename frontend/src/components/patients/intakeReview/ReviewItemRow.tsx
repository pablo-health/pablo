// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"

import type {
  IntakeChartArtifact,
  IntakeReviewItem,
  IntakeReviewSignature,
} from "@/lib/api/intakeReview"
import type { IntakeForm } from "@/lib/api/patientIntake"
import { viewFor } from "./registry"
import { questionOf } from "./ViewParts"

const COPY = {
  provenance: { patient: "Patient", clinician: "Entered by practice" },
  earlier: (n: number) => (n === 1 ? "1 earlier answer" : `${n} earlier answers`),
  earlierDetail: (n: number) => (n === 1 ? "One earlier answer was replaced." : `${n} earlier answers were replaced.`),
  enter: "Enter for patient",
  entryLabel: "Answer",
  entrySave: "Save",
  entryCancel: "Cancel",
}

const LINK = "text-xs font-medium text-primary-600 hover:text-primary-700"
const CHIP = "mt-2 inline-block rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600"

export interface ReviewItemRowProps {
  item: IntakeReviewItem
  form: IntakeForm
  signatures: IntakeReviewSignature[]
  artifacts: IntakeChartArtifact[]
  selectable: boolean
  selected: boolean
  onSelect: (itemId: string, checked: boolean) => void
  canEnter: boolean
  saving: boolean
  onSaveEntry: (itemId: string, text: string) => void
}

/**
 * One question as the patient saw it, and what the practice can do with it.
 *
 * The question and its answer are drawn by the view for its type. Around it
 * sit the review's own facts — who put the answer there, how many it
 * replaced — and the two actions, which a heading or a paragraph never
 * offers: there is nothing on one to send back or to write down.
 */
export function ReviewItemRow(props: ReviewItemRowProps) {
  const { item, form, signatures, artifacts, selectable, selected, onSelect, canEnter, saving, onSaveEntry } = props
  const [showEarlier, setShowEarlier] = useState(false)
  const [entryOpen, setEntryOpen] = useState(false)
  const [entryText, setEntryText] = useState("")
  const id = item.id
  const view = viewFor(item.item_type)
  const View = view.Component

  return (
    <li className="border-t border-border py-3 first:border-t-0" data-testid={`intake-review-item-${id}`}>
      <div className="flex items-start gap-3">
        {selectable && view.answerable && (
          <input type="checkbox" className="mt-1" checked={selected} aria-label={questionOf(item)}
            onChange={(e) => onSelect(id, e.target.checked)} data-testid={`intake-review-select-${id}`} />
        )}
        <div className="min-w-0 flex-1">
          <div data-testid={`intake-review-value-${id}`}>
            <View item={item} form={form} signatures={signatures} artifacts={artifacts} />
          </div>
          {item.provenance && (
            <span className={CHIP} data-testid={`intake-review-provenance-${id}`}>
              {COPY.provenance[item.provenance]}
            </span>
          )}
          {item.superseded_count > 0 && (
            <div className="mt-2">
              <button type="button" className={LINK} aria-expanded={showEarlier}
                onClick={() => setShowEarlier((open) => !open)}
                data-testid={`intake-review-earlier-toggle-${id}`}>
                {COPY.earlier(item.superseded_count)}
              </button>
              {showEarlier && (
                <p className="mt-1 text-xs text-neutral-500" data-testid={`intake-review-earlier-detail-${id}`}>
                  {COPY.earlierDetail(item.superseded_count)}
                </p>
              )}
            </div>
          )}
          {canEnter && view.answerable && !entryOpen && (
            <button type="button" className={`mt-2 block ${LINK}`} onClick={() => setEntryOpen(true)}
              data-testid={`intake-review-enter-${id}`}>
              {COPY.enter}
            </button>
          )}
          {canEnter && view.answerable && entryOpen && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
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
