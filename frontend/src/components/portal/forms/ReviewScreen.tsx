// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every question and what was said, with a way back into each one.
 *
 * The last screen before the form leaves the patient's hands, so it is the
 * one that has to be readable rather than complete: headings and paragraphs
 * are left out, because there is nothing to check about a sentence somebody
 * read.
 *
 * **It never says the form is finished.** Sending is offered whatever the
 * rows look like, and whether the form can go is the server's answer — it
 * refuses an unfinished one and names what is outstanding. A screen that
 * decided for itself would be deciding about questions it cannot see.
 *
 * **A form sent back reads as one question, not a whole form again.** When
 * a correction is open the heading says who is asking, the practice's own
 * note sits under it, and the list is the questions they named. The note is
 * shown as it was written; nothing here paraphrases it.
 */

import { Button } from "@/components/ui/button"
import type {
  IntakeAssignmentItem,
  IntakeCorrection,
  IntakeForm,
} from "@/lib/api/patientIntake"
import {
  BACK,
  CORRECTION_SUBMIT,
  EDIT,
  REVIEW_BODY,
  REVIEW_HEADING,
  REVIEW_UNANSWERED,
  SUBMIT,
  SUBMITTING,
  correctionHeading,
} from "./formsCopy"
import { rendererFor } from "./renderers/registry"
import type { AnswerValue } from "./renderers/types"

interface ReviewScreenProps {
  items: IntakeAssignmentItem[]
  values: Record<string, AnswerValue | null>
  form: IntakeForm | null
  /** Set while the practice has sent the form back. Absent otherwise. */
  correction?: IntakeCorrection | null
  onEdit: (itemId: string) => void
  /** Absent when there is no question to step back to. */
  onBack: (() => void) | null
  onSubmit: () => void
  submitting: boolean
  error: string | null
}

export function ReviewScreen({
  items,
  values,
  form,
  correction = null,
  onEdit,
  onBack,
  onSubmit,
  submitting,
  error,
}: ReviewScreenProps) {
  // Every question that collects something, including the ones whose
  // renderer wrote through a route of its own — a consent document belongs
  // on this list as much as an answer does.
  const answerable = items.filter((item) => {
    const renderer = rendererFor(item.item_type)
    return renderer.answerable || renderer.writesItself === true
  })

  return (
    <div data-testid="forms-review" className="flex flex-col">
      <h2 className="text-lg font-semibold text-neutral-900">
        {correction ? correctionHeading(correction.item_ids.length) : REVIEW_HEADING}
      </h2>
      {correction ? (
        correction.note && (
          <p
            data-testid="forms-correction-note"
            className="mt-2 whitespace-pre-line rounded-md border border-neutral-200 bg-neutral-50 p-3 text-sm text-neutral-800"
          >
            {correction.note}
          </p>
        )
      ) : (
        <p className="mt-2 text-sm text-neutral-600">{REVIEW_BODY}</p>
      )}

      <ul className="mt-5 flex flex-col divide-y divide-neutral-200 border-y border-neutral-200">
        {answerable.map((item) => (
          <ReviewRow
            key={item.id}
            item={item}
            value={values[item.id] ?? null}
            form={form}
            onEdit={onEdit}
          />
        ))}
      </ul>

      {error && (
        <p data-testid="forms-submit-error" className="mt-4 text-sm text-red-600">
          {error}
        </p>
      )}

      <div className="mt-6 flex gap-2">
        {onBack && (
          <Button
            variant="outline"
            data-testid="forms-review-back"
            disabled={submitting}
            onClick={onBack}
          >
            {BACK}
          </Button>
        )}
        <Button
          className="flex-1"
          size="lg"
          data-testid="forms-submit"
          disabled={submitting}
          onClick={onSubmit}
        >
          {submitting ? SUBMITTING : correction ? CORRECTION_SUBMIT : SUBMIT}
        </Button>
      </div>
    </div>
  )
}

function ReviewRow({
  item,
  value,
  form,
  onEdit,
}: {
  item: IntakeAssignmentItem
  value: AnswerValue | null
  form: IntakeForm | null
  onEdit: (itemId: string) => void
}) {
  const renderer = rendererFor(item.item_type)
  const summary = renderer.summary(value, item, form)
  return (
    <li data-testid={`forms-review-row-${item.id}`} className="flex gap-3 py-3">
      <div className="flex-1">
        <p className="text-xs font-medium text-neutral-500">{renderer.label(item, form)}</p>
        <p className="mt-1 whitespace-pre-line text-sm text-neutral-800">
          {summary ?? <span className="text-neutral-500">{REVIEW_UNANSWERED}</span>}
        </p>
      </div>
      <button
        type="button"
        data-testid="forms-review-edit"
        className="self-start text-sm font-medium text-primary-700 underline"
        onClick={() => onEdit(item.id)}
      >
        {EDIT}
      </button>
    </li>
  )
}
